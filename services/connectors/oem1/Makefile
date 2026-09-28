REPO_ROOT := $(abspath $(CURDIR)/../../..)
# Use venv python if available; fallback to system python3
VENV_PYTHON := $(REPO_ROOT)/.venv/bin/python3
PYTHON := $(if $(wildcard $(VENV_PYTHON)),$(VENV_PYTHON),python3)

.PHONY: protos-oem1 test clean

protos-oem1:
	$(PYTHON) $(REPO_ROOT)/scripts/rename-oem1-protos.py
	mkdir -p _generated
	$(PYTHON) -m grpc_tools.protoc \
		--proto_path=proto \
		--python_out=_generated \
		--grpc_python_out=_generated \
		$$(find proto -name '*.proto')
	# Remove google/__init__.py so google.type/rpc/api stubs coexist with
	# site-packages google.protobuf via the namespace-package mechanism.
	rm -f _generated/google/__init__.py

test:
	$(PYTHON) -m pytest tests/ -v

clean:
	rm -rf _generated
