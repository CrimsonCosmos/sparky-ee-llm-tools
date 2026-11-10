#!/bin/bash
# usage: score.sh RUN PROBLEM FILE.v   -> scores FILE.v against the hidden reference, logs the attempt, prints tool feedback
RUN=$1; P=$2; F=$3
mkdir -p /home/user/eval/runs/$RUN
cd /tmp && PYTHONPATH=/home/user/sparky-ee-llm-tools/src /home/user/venv/bin/python -m hdlbench score $P "$F" --log /home/user/eval/runs/$RUN/$P.jsonl
