TIER := T1
LAMBDA_DIR := $(CURDIR)/poll-usgs
LAMBDA_S3_URL := s3://pl-amit/lambda/poll-usgs.zip
TEMPLATE_NAME := landsat-ingestor.template
STACK_NAME := landsat-ingestor-temp
TIMESTAMP := $(shell date +"%Y%m%d%H%M%S")
TEMPLATE_S3_URL = s3://pl-amit/templates/landsat-ingestor-$(TIMESTAMP).template
TEMPLATE_PUBLIC_URL = https://pl-amit.s3.amazonaws.com/templates/landsat-ingestor-$(TIMESTAMP).template
OWNER := amitkapadia

PROCESSOR_STACK_NAME := landsat-ingestor-processor
PROCESSOR_TEMPLATE_NAME := landsat-ingestor-processor.template
PROCESSOR_TEMPLATE_S3_URL = s3://pl-amit/templates/landsat-ingestor-processor-$(TIMESTAMP).template
PROCESSOR_TEMPLATE_PUBLIC_URL = https://pl-amit.s3.amazonaws.com/templates/landsat-ingestor-processor-$(TIMESTAMP).template
PROCESSOR_LAMBDA_DIR := $(CURDIR)/poll-s3
PROCESSOR_LAMBDA_S3_URL := s3://pl-amit/lambda/poll-s3.zip

# Upload lambda dependency to S3
lambda:
	pip install requests -t $(LAMBDA_DIR)
	pip install usgs -t $(LAMBDA_DIR)
	cd $(LAMBDA_DIR); zip -r poll-usgs.zip .;
	aws s3 cp $(LAMBDA_DIR)/poll-usgs.zip $(LAMBDA_S3_URL)
	rm $(LAMBDA_DIR)/poll-usgs.zip

lambda-update:
	aws lambda update-function-code \
	--function-name landsat-ingestor-T1 \
	--s3-bucket pl-amit \
	--s3-key lambda/poll-usgs.zip \
	--region us-west-2

	aws lambda update-function-code \
	--function-name landsat-ingestor-RT \
	--s3-bucket pl-amit \
	--s3-key lambda/poll-usgs.zip \
	--region us-west-2

cloudformation:
	@aws s3 cp $(TEMPLATE_NAME) $(TEMPLATE_S3_URL) --acl public-read
	@aws cloudformation create-stack \
		--stack-name $(STACK_NAME)-$(TIMESTAMP) \
		--template-url $(TEMPLATE_PUBLIC_URL) \
		--parameters \
			ParameterKey=Name,ParameterValue=$(STACK_NAME) \
			ParameterKey=Owner,ParameterValue=$(OWNER) \
			ParameterKey=USGSUsername,ParameterValue=$(USGS_USERNAME) \
			ParameterKey=USGSPassword,ParameterValue=$(USGS_PASSWORD) \
			ParameterKey=Tier,ParameterValue=$(TIER) \
		--capabilities CAPABILITY_IAM \
		--region us-west-2

processor-cloudformation:
	@aws s3 cp $(PROCESSOR_TEMPLATE_NAME) $(PROCESSOR_TEMPLATE_S3_URL) --acl public-read
	@aws cloudformation create-stack \
		--stack-name $(PROCESSOR_STACK_NAME)-$(TIMESTAMP) \
		--template-url $(PROCESSOR_TEMPLATE_PUBLIC_URL) \
		--parameters \
			ParameterKey=Name,ParameterValue=$(PROCESSOR_STACK_NAME) \
			ParameterKey=Owner,ParameterValue=$(OWNER) \
		--capabilities CAPABILITY_IAM \
		--region us-west-2

processor-lambda:
	cd $(PROCESSOR_LAMBDA_DIR); zip -r poll-s3.zip .;
	aws s3 cp $(PROCESSOR_LAMBDA_DIR)/poll-s3.zip $(PROCESSOR_LAMBDA_S3_URL)
	rm $(PROCESSOR_LAMBDA_DIR)/poll-s3.zip

clean:
	find poll-usgs/ -not -name 'handler.py' -not -name 'poll-usgs' | xargs rm -rf --