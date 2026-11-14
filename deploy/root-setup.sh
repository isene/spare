#!/bin/sh
# Root part of the server setup, for a Debian or Ubuntu server with Apache and certbot.
# Run once, from the app folder:
#   sudo sh deploy/root-setup.sh spare.example.com you@example.com
# The name must point at this server first. The mail address gets certificate warnings.
set -e
HOST=${1:?Give the site name, such as spare.example.com}
MAIL=${2:?Give a mail address for certificate warnings}
APP=$(cd "$(dirname "$0")/.." && pwd)
OWNER=$(stat -c %U "$APP")
PORT=$(sed -n 's/^SPARE_PORT=//p' "$APP/.env" 2>/dev/null)
PORT=${PORT:-8094}
SITE=/etc/apache2/sites-available/spare.conf

SERVER_IP=$(hostname -I | awk '{print $1}')
DNS_IP=$(getent ahostsv4 "$HOST" | awk 'NR==1{print $1}')
if [ "$DNS_IP" != "$SERVER_IP" ]; then
  echo "Stop: $HOST points to '${DNS_IP:-nothing}', and this server is $SERVER_IP."
  echo "Add an A record for $HOST with $SERVER_IP, wait a few minutes, then run this again."
  exit 1
fi

apt-get -o DPkg::Lock::Timeout=600 install -y -q python3-venv

# The app's Python packages, owned by the app's user.
cd "$APP"
test -x .venv/bin/pip || { rm -rf .venv; sudo -H -u "$OWNER" python3 -m venv .venv; }
sudo -H -u "$OWNER" .venv/bin/pip install -q -U pip
sudo -H -u "$OWNER" .venv/bin/pip install -q -r requirements.txt

cat > /etc/systemd/system/spare.service <<EOF
[Unit]
Description=spare
After=network.target

[Service]
User=$OWNER
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/python app.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now spare

# Lets the owner restart the app after a git pull, without a password.
echo "$OWNER ALL=(root) NOPASSWD: /usr/bin/systemctl restart spare, /bin/systemctl restart spare" > /etc/sudoers.d/spare
chmod 440 /etc/sudoers.d/spare
visudo -cf /etc/sudoers.d/spare

redirect_vhost() {
  cat <<EOF
<VirtualHost *:80>
  ServerName $HOST
  DocumentRoot /var/www/letsencrypt
  RedirectMatch permanent ^/(?!\.well-known/)(.*)\$ https://$HOST/\$1
</VirtualHost>
EOF
}

mkdir -p /var/www/letsencrypt
redirect_vhost > $SITE
a2enmod -q proxy proxy_http headers ssl
a2ensite -q spare
apache2ctl configtest
systemctl reload apache2

certbot certonly --webroot -w /var/www/letsencrypt -d "$HOST" --non-interactive \
  --agree-tos -m "$MAIL" --deploy-hook "systemctl reload apache2"

{ redirect_vhost; cat <<EOF
<VirtualHost *:443>
  ServerName $HOST
  SSLEngine on
  SSLCertificateFile /etc/letsencrypt/live/$HOST/fullchain.pem
  SSLCertificateKeyFile /etc/letsencrypt/live/$HOST/privkey.pem
  Include /etc/letsencrypt/options-ssl-apache.conf
  Header always set Strict-Transport-Security "max-age=31536000"
  RequestHeader set X-Forwarded-Proto "https"
  ProxyPreserveHost On
  ProxyPass / http://127.0.0.1:$PORT/
  ProxyPassReverse / http://127.0.0.1:$PORT/
  LimitRequestBody 1048576
</VirtualHost>
EOF
} > $SITE
apache2ctl configtest
systemctl reload apache2
echo "spare is live at https://$HOST"
