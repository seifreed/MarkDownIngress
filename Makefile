PYTEST ?= python -m pytest
URL_BASELINE_LIMIT ?= 250
URL_CAMPAIGN_LIMIT ?= 50000
URL_CAMPAIGN_SCENARIOS ?=
URL_CAMPAIGN_CONCURRENCY ?= 32
URL_CAMPAIGN_BATCH_SIZE ?= 64
URL_CAMPAIGN_RESUME_DIR ?=

.PHONY: test test-fast test-baseline test-campaign test-campaign-resume

test:
	$(PYTEST) -q

test-fast:
	$(PYTEST) -q -m "not baseline and not campaign"

test-baseline:
	$(PYTEST) -q -o addopts='' tests/test_url_dataset_baseline.py \
		--run-url-baseline \
		--url-baseline-limit "$(URL_BASELINE_LIMIT)"

test-campaign:
	$(PYTEST) -q -o addopts='' tests/test_url_dataset_campaign.py \
		--run-url-campaign \
		--url-campaign-limit "$(URL_CAMPAIGN_LIMIT)" \
		--url-campaign-concurrency "$(URL_CAMPAIGN_CONCURRENCY)" \
		--url-campaign-batch-size "$(URL_CAMPAIGN_BATCH_SIZE)" \
		$(if $(URL_CAMPAIGN_SCENARIOS),--url-campaign-scenarios "$(URL_CAMPAIGN_SCENARIOS)",)

test-campaign-resume:
	$(if $(URL_CAMPAIGN_RESUME_DIR),,$(error URL_CAMPAIGN_RESUME_DIR is required))
	$(PYTEST) -q -o addopts='' tests/test_url_dataset_campaign.py \
		--run-url-campaign \
		--url-campaign-limit "$(URL_CAMPAIGN_LIMIT)" \
		--url-campaign-concurrency "$(URL_CAMPAIGN_CONCURRENCY)" \
		--url-campaign-batch-size "$(URL_CAMPAIGN_BATCH_SIZE)" \
		--url-campaign-resume-dir "$(URL_CAMPAIGN_RESUME_DIR)" \
		$(if $(URL_CAMPAIGN_SCENARIOS),--url-campaign-scenarios "$(URL_CAMPAIGN_SCENARIOS)",)
