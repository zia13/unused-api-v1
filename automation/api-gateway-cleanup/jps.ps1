node('windows'){
stage('create script') {
writeFile file: 'CreateCatalog.ps1', text: '''
        param(
            [string]$accountId,
            [string]$accountName,
            [int]$index,
            [string]$env
        )

        # Helper function to get CloudWatch request count
        function Get-ApiRequestCount {
            param(
                [string]$apiId,
                [string]$stageName,
                [int]$days = 10
            )

            Write-Host "Inside Get-ApiRequestCount ---> Getting CloudWatch metrics for API ID: $($apiId) Stage: $($stageName)" -ForegroundColor Cyan
            $endTime = (Get-Date).ToUniversalTime()
            $startTime = $endTime.AddDays(-$days)
            Write-Host "Start Time: $($startTime.ToString('yyyy-MM-ddTHH:mm:ssZ')) End Time: $($endTime.ToString('yyyy-MM-ddTHH:mm:ssZ'))" -ForegroundColor Cyan

            try {
                # List all available Count metrics for this API and Stage
                Write-Host "Listing all available Count metrics for API $apiId stage $stageName..." -ForegroundColor Cyan
                $availableMetricsJson = aws cloudwatch list-metrics `
                    --namespace AWS/ApiGateway `
                    --metric-name Count `
                    --dimensions Name=ApiId,Value=$apiId Name=Stage,Value=$stageName `
                    --region us-east-1 `
                    --output json

                $availableMetrics = $availableMetricsJson | ConvertFrom-Json
                Write-Host "Found $($availableMetrics.Metrics.Count) Count metrics" -ForegroundColor Cyan

                if (-not $availableMetrics.Metrics -or $availableMetrics.Metrics.Count -eq 0) {
                    Write-Host "No CloudWatch Count metrics found for API $apiId stage $stageName" -ForegroundColor Yellow
                    return 0
                }

                # Query each unique dimension combination
                $totalCount = 0
                $processedDimensions = @{}

                foreach($metric in $availableMetrics.Metrics) {
                    # Create a unique key for this dimension combination
                    $sortedDims = $metric.Dimensions | Sort-Object -Property Name
                    $dimKey = ($sortedDims | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join "|"

                    if ($processedDimensions.ContainsKey($dimKey)) {
                        continue
                    }
                    $processedDimensions[$dimKey] = $true

                    # Build dimensions array for AWS CLI
                    $dimArgs = @()
                    foreach($dim in $sortedDims) {
                        $dimArgs += "Name=$($dim.Name),Value=$($dim.Value)"
                    }

                    Write-Host "  Querying dimensions: $dimKey" -ForegroundColor Gray

                    # Query this specific dimension combination
                    $metricDataJson = aws cloudwatch get-metric-statistics `
                        --namespace AWS/ApiGateway `
                        --metric-name Count `
                        --dimensions $dimArgs `
                        --start-time $startTime.ToString("yyyy-MM-ddTHH:mm:ssZ") `
                        --end-time $endTime.ToString("yyyy-MM-ddTHH:mm:ssZ") `
                        --period 86400 `
                        --statistics Sum `
                        --region us-east-1 `
                        --output json

                    $metricData = $metricDataJson | ConvertFrom-Json

                    if ($metricData.Datapoints -and $metricData.Datapoints.Count -gt 0) {
                        $subtotal = 0
                        foreach($datapoint in $metricData.Datapoints) {
                            $subtotal += $datapoint.Sum
                        }
                        Write-Host "    Found $subtotal requests for $dimKey" -ForegroundColor Green
                        $totalCount += $subtotal
                    }
                }

                Write-Host "Total count for API $apiId stage $stageName: $totalCount" -ForegroundColor Green
                return [math]::Round($totalCount)
            } catch {
                Write-Host "Warning: Failed to get metrics for API $apiId stage $stageName - Error: $_" -ForegroundColor Red
                Write-Host "Error details: $($_.Exception.Message)" -ForegroundColor Red
                return 0
            }
        }

        $apimap = @{}
        $apilist = aws apigateway get-rest-apis | ConvertFrom-Json
        foreach($api in $apilist.items) {
            $apimap[$api.id] = $api.name
        }

        $vpclinkmap = @{}
        $vpclinklist = aws apigateway get-vpc-links | ConvertFrom-Json
        foreach($vpclink in $vpclinklist.items) {
            $vpclinkmap[$vpclink.id] = $vpclink.name
        }
        Write-Host "Index,API Name,API Id,basePath,stage,domainName,Authorizer Name,Integration Type,Integration Uri,Integration Timeout,Account Name,Account Id,Count"
        $domains = aws apigateway get-domain-names | ConvertFrom-Json
        foreach($domain in $domains.items) {
            #Write-Host "$($domain)"
            if (-not $domain.domainName.EndsWith("apigateway.corebridgefinancial.com")) {
                continue
            }
            $mappings = aws apigateway get-base-path-mappings --domain-name $domain.domainName | ConvertFrom-Json
            foreach ($mapping in $mappings.items) {
                if ($mapping.basepath -eq "(none)" -or [string]::IsNullOrEmpty($mapping.basepath)) {
                    continue
                }
                #Write-Host "$($mapping)"
                $spec = aws apigateway get-export `
                    --rest-api-id $mapping.restApiId `
                    --stage-name $mapping.stage `
                    --export-type oas30 `
                    --parameters extensions='apigateway' `
                    output.json
                try {
                    $content = Get-Content output.json -Raw | ConvertFrom-Json
                } catch {
                    #dghjdg
                }

                $authorizeruris = $content.components.securitySchemes.PSObject.Properties | ForEach-Object {
                    $_.Value."x-amazon-apigateway-authorizer".authorizerUri
                }
                $lambdaAuthorizerName = ""
                $authorizeruris | ForEach-Object {
                    if($_ -match "function:([^/]+)") {
                        $lambdaAuthorizerName = $Matches[1]
                    }
                }

                $firstpathprop = $content.paths.PSObject.Properties | Select-Object -First 1
                $pathname = $firstpathprop.Name

                $firstmethodprop = $firstpathprop.Value.PSObject.Properties | Select-Object -First 1
                $methodname = $firstmethodprop.Name

                $integrationType = ""
                $integrationUri = ""
                $integrationConnectionId = ""
                $timeoutInMillis = ""
                $integration = $firstmethodprop.Value."x-amazon-apigateway-integration"
                if($integration) {
                    $integrationType = $integration.type
                    $integrationUri = $integration.uri
                    $integrationConnectionId = $integration.connectionId
                    $timeoutInMillis = $integration.timeoutInMillis
                    $integrationConnectionType = $integration.connectionType
                    if($integrationConnectionType -eq "VPC_LINK" -and $integrationConnectionId) {
                        $integrationType = "vpc link"
                        $integrationUri = $vpclinkmap[$integrationConnectionId]
                    }
                    if($integrationType -eq "aws_proxy" -and $integrationUri -match "function:([^/]+)") {
                        $integrationType = "lambda"
                        $integrationUri = $Matches[1]
                    }
                }

                # Get CloudWatch request count for the last 90 days
                Write-Host "Getting CloudWatch metrics for API Id: $($mapping.restApiId) Stage: $($mapping.stage)" -ForegroundColor Cyan
                $requestCount = Get-ApiRequestCount -apiId $mapping.restApiId -stageName $mapping.stage

                $index++
                Write-Host "$index,$($apimap[$mapping.restApiId]),$($mapping.restApiId),$($mapping.basePath),$($mapping.stage),$($domain.domainName),$($lambdaAuthorizerName),$($integrationType),$($integrationUri),$($timeoutInMillis),$($accountName),$($accountId),$requestCount"
                $result = [PSCustomObject]@{
                    Index = $index
                    ApiName = $apimap[$mapping.restApiId]
                    ApiId = $mapping.restApiId
                    Basepath = $mapping.basePath
                    Stage = $mapping.stage
                    Domain = $domain.domainName
                    Authorizer = $lambdaAuthorizerName
                    IntegrationType = $integrationType
                    IntegrationUri = $integrationUri
                    IntegrationTimeout = $timeoutInMillis
                    AccountId = $accountId
                    AccountName = $accountName
                    Count = $requestCount
                }
                $filename = "${env}_apigateway_report.csv"
                $result | Export-Csv $filename -Append -NoTypeInformation
            }
        }
        return $index
        '''
}
stage('Remove old csv') {
powershell """
        Remove-Item apigateway_report.csv -ErrorAction SilentlyContinue
        """
}
stage('Create Catalog'){
def awsDefaultRegion = "us-east-1"
def awsDefaultOutput = "json"
def awsAccountsArr = (params.TriggerType = = "Manual")?params.awsAccounts.split(','): params.PeriodicAwsAccounts.split(',')
def environment = params.Environment
echo "awsAccounts :: ${awsAccountsArr
}"
echo "Environment :: ${environment
}"
def serialno = 0
for (awsAccountWithName in awsAccountsArr) {
def awsAccount = awsAccountWithName.split('/')
def awsAccountName = awsAccount[0]
def awsAccountID = awsAccount[1]
withEnv(["AWS_DEFAULT_REGION=${awsDefaultRegion
}", "AWS_DEFAULT_OUTPUT=${awsDefaultOutput
}"]) {
withCredentials([[$class: 'StringBinding', credentialsId: "${awsAccountID
}-lrdevops-aws-access-key", variable: 'AWS_ACCESS_KEY_ID'], [$class: 'StringBinding', credentialsId: "${awsAccountID
}-lrdevops-aws-secret-key", variable: 'AWS_SECRET_ACCESS_KEY']])
{
echo "cmdResponse :::${awsAccountID
} ${awsAccountName
}"
serialno = powershell(
script: """
                        .\\CreateCatalog.ps1 -accountId ${awsAccountID
} -accountName "${awsAccountName
}" -index ${serialno
} -env ${environment
}
                        """,
returnStdout: true
).trim().toInteger()
}
}
}
}
stage('Archive csv'){
archiveArtifacts artifacts: '**/*.csv',
fingerprint: true
}
}