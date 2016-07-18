
LAMBDA_DIR := $(CURDIR)/poll-usgs
LAMBDA_S3_URL := s3://pl-amit/lambda/poll-usgs.zip
STACK_NAME := landsat-ingestor
TIMESTAMP := $(shell date +"%Y%m%d%H%M%S")
TEMPLATE_S3_URL = s3://pl-amit/templates/$(STACK_NAME)-$(TIMESTAMP).template
TEMPLATE_PUBLIC_URL = https://pl-amit.s3.amazonaws.com/templates/$(STACK_NAME)-$(TIMESTAMP).template
OWNER := amitkapadia


lambda:
	pip install requests -t $(LAMBDA_DIR)
	cd $(LAMBDA_DIR); zip -r poll-usgs.zip .;
	aws s3 cp $(LAMBDA_DIR)/poll-usgs.zip $(LAMBDA_S3_URL)
	rm $(LAMBDA_DIR)/poll-usgs.zip

cloudformation:
	@aws s3 cp $(STACK_NAME).template $(TEMPLATE_S3_URL) --acl public-read
	@aws cloudformation create-stack \
		--stack-name landsat-ingestor-$(TIMESTAMP) \
		--template-url $(TEMPLATE_PUBLIC_URL) \
		--parameters \
			ParameterKey=Name,ParameterValue=$(STACK_NAME) \
			ParameterKey=Owner,ParameterValue=$(OWNER) \
			ParameterKey=USGSUsername,ParameterValue=$(USGS_USERNAME) \
			ParameterKey=USGSPassword,ParameterValue=$(USGS_PASSWORD) \
		--capabilities CAPABILITY_IAM \
		--region us-west-2

clean:
	find poll-usgs/ -not -name 'handler.py' -not -name 'poll-usgs' | xargs rm -rf --