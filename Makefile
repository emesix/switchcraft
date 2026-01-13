# Switchcraft Makefile
# Hardware-in-the-Loop (HIL) testing and development tasks

.PHONY: help test lint hil hil-brocade hil-zyxel hil-openwrt clean

# Default target
help:
	@echo "Switchcraft Development Tasks"
	@echo ""
	@echo "Testing:"
	@echo "  make test          Run unit tests"
	@echo "  make test-quick    Run unit tests (fast, skip slow)"
	@echo "  make lint          Run linter (ruff)"
	@echo ""
	@echo "HIL Testing (Hardware-in-the-Loop):"
	@echo "  make hil           Run HIL tests on ALL devices (192.168.254.2-4)"
	@echo "  make hil-brocade   Run HIL test on Brocade only (254.2)"
	@echo "  make hil-zyxel     Run HIL test on Zyxel only (254.3)"
	@echo "  make hil-openwrt   Run HIL test on OpenWrt only (254.4)"
	@echo "  make hil-report    Show last HIL report"
	@echo ""
	@echo "Development:"
	@echo "  make server        Start MCP server"
	@echo "  make clean         Clean build artifacts"
	@echo ""
	@echo "Environment:"
	@echo "  NETWORK_PASSWORD   Device credentials (required for HIL)"
	@echo "  SWITCHCRAFT_HIL_MODE=1  Enable HIL constraints (auto for 'make hil')"

# Unit tests
test:
	pytest tests/ -v --ignore=tests/test_integration.py

test-quick:
	pytest tests/ -v --ignore=tests/test_integration.py --ignore=tests/test_git_integration.py -x

# Linting
lint:
	ruff check src/ tests/

lint-fix:
	ruff check src/ tests/ --fix

# HIL Testing - VLAN 999 only, devices 192.168.254.2-4
# These targets automatically enable HIL mode constraints

hil: check-env
	@echo "============================================================"
	@echo "HIL (Hardware-in-the-Loop) Test - ALL DEVICES"
	@echo "VLAN: 999 (enforced)"
	@echo "Devices: 192.168.254.2, 192.168.254.3, 192.168.254.4"
	@echo "============================================================"
	SWITCHCRAFT_HIL_MODE=1 python -m mcp_network_switch.hil.cli

hil-brocade: check-env
	@echo "HIL Test - Brocade (192.168.254.2)"
	SWITCHCRAFT_HIL_MODE=1 python -m mcp_network_switch.hil.cli --device lab-brocade

hil-zyxel: check-env
	@echo "HIL Test - Zyxel (192.168.254.3)"
	SWITCHCRAFT_HIL_MODE=1 python -m mcp_network_switch.hil.cli --device lab-zyxel

hil-openwrt: check-env
	@echo "HIL Test - OpenWrt (192.168.254.4)"
	SWITCHCRAFT_HIL_MODE=1 python -m mcp_network_switch.hil.cli --device lab-openwrt

hil-verbose: check-env
	SWITCHCRAFT_HIL_MODE=1 python -m mcp_network_switch.hil.cli -v

hil-report:
	@latest=$$(ls -td artifacts/hil/*/ 2>/dev/null | head -1); \
	if [ -n "$$latest" ]; then \
		echo "Latest HIL Report: $$latest"; \
		cat "$$latest/hil-report.json" | python -m json.tool; \
	else \
		echo "No HIL reports found. Run 'make hil' first."; \
	fi

# Environment check
check-env:
ifndef NETWORK_PASSWORD
	$(error NETWORK_PASSWORD is not set. Run: source .env)
endif

# Development server
server:
	python -m mcp_network_switch.server

# Clean artifacts
clean:
	rm -rf artifacts/hil/*
	rm -rf .pytest_cache
	rm -rf __pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
