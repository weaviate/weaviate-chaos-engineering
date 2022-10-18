#!/bin/bash

# get weaviate version tags without cloning the repo
git -c 'versionsort.suffix=-' ls-remote --tags --sort='v:refname' https://github.com/semi-technologies/weaviate.git | cut -d/ -f3 | grep -E 'v[1-9].[0-9]{1,2}.[0-9]$'
