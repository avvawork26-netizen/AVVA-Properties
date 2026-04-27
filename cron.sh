#!/bin/bash
# AVVA Properties — paste these lines into your crontab with: crontab -e
# Make sure to update /path/to/avva-properties to the actual project path
# Create the logs directory first: mkdir -p /path/to/avva-properties/logs

# Run scraper nightly at 2am
0 2 * * * cd /path/to/avva-properties && python scraper.py >> logs/scraper.log 2>&1

# Run skip tracer at 2:30am
30 2 * * * cd /path/to/avva-properties && python skiptracer.py >> logs/skiptracer.log 2>&1

# Run outreach every morning at 9am
0 9 * * * cd /path/to/avva-properties && python outreach.py >> logs/outreach.log 2>&1
