#!/bin/bash

python -m uvicorn barcode_gen:app --host 0.0.0.0 --port 8000
