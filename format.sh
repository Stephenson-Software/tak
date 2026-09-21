#!/bin/bash
black src tests
autoflake --in-place --remove-all-unused-imports --remove-unused-variables -r src tests
