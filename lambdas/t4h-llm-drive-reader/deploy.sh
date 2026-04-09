#!/bin/bash
# Deploy t4h-llm-drive-reader to Lambda
# Run from this directory: bash deploy.sh
set -e
FUNC="t4h-llm-drive-reader"
REGION="ap-southeast-2"
ROLE="arn:aws:iam::140548542136:role/lambda-execution-role"
SUPABASE_URL="https://lzfgigiyqpuuxslsygjt.supabase.co"
SUPABASE_KEY="eyJhbGci...SET_FROM_CAP_SECRETS"

echo "Installing deps..."
pip install -r requirements.txt -t package/ -q
cp lambda_function.py package/
cd package && zip -r9 ../function.zip . -q && cd ..
echo "Deploying..."
aws lambda create-function \
  --function-name $FUNC --runtime python3.11 \
  --role $ROLE --handler lambda_function.lambda_handler \
  --zip-file fileb://function.zip \
  --timeout 300 --memory-size 512 --region $REGION \
  --environment "Variables={SUPABASE_URL=$SUPABASE_URL,SUPABASE_SERVICE_KEY=PASTE_FROM_CAP_SECRETS}" \
  2>/dev/null || \
aws lambda update-function-code --function-name $FUNC \
  --zip-file fileb://function.zip --region $REGION
echo "Done."
