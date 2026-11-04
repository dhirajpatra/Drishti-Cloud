# Drishti edge camera agent

Runs at the camera's site and **pushes** pictures to your Drishti server over HTTPS. No VPN, no ngrok, no open ports, and the camera password never leaves the site.

```bash
cp .env.example .env        # GATEWAY_URL, TENANT_ID, EDGE_TOKEN (Admin > Connect a camera > PAIR A SITE), CAMERAS
docker compose run --rm camera-agent python agent.py --check    # one picture per camera: OK / FAIL
docker compose up -d --build
```

Without Docker: `pip install -r requirements.txt` then `python agent.py` with the same variables in the environment.

Full guide, other options (VPN, tunnel) and troubleshooting: [../../docs/REMOTE_CAMERAS.md](../../docs/REMOTE_CAMERAS.md).
