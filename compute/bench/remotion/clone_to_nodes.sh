# SPDX-License-Identifier: MIT
SP="sshpass -p vista1 ssh -o StrictHostKeyChecking=no"
NODEDIR=$($SP root@172.31.4.1 'readlink -f /opt/node' | xargs basename)
for ip in 172.31.5.1 172.31.6.1; do
  ( $SP root@172.31.4.1 "tar cf - -C /opt csbench $NODEDIR" | $SP root@$ip "rm -rf /opt/csbench && tar xf - -C /opt && ln -sfn /opt/$NODEDIR /opt/node && for b in node npm npx corepack; do ln -sf /opt/node/bin/\$b /usr/local/bin/\$b; done && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 libxshmfence1 fonts-liberation fonts-noto-color-emoji >/dev/null 2>&1; echo \$(hostname -I | cut -d' ' -f1) node \$(node -v) \$(du -sh /opt/csbench | cut -f1)" ) &
done; wait
