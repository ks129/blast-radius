#!/bin/bash
# sync local sub/ (minus target) to VM ~/sub and build
cd /Users/karlissuvi/blast-radius/sub
COPYFILE_DISABLE=1 tar --no-xattrs -czf - --exclude target --exclude '*.gz' selector app tools Dockerfile | ssh blastvm 'mkdir -p ~/sub && cd ~/sub && tar xzf - && export PATH=$HOME/.cargo/bin:$PATH && cd selector && cargo build --release 2>&1 | grep -E "^(error|warning)" -A6 | head -40; cp -f ~/map.txt ~/sub/app/map.txt 2>/dev/null; cp -f ~/sub/tools/*.py ~/sub/tools/*.sh ~/tools/'
