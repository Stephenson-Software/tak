#!/bin/bash
# Usage: ./test.sh
python3 -m pytest --verbose -vv --cov=src --cov-report=term-missing --cov-report=xml:cov.xml
