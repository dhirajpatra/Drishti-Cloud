# Connecting a private RTSP camera from anywhere

Home, mall, school and hospital cameras sit on **private** addresses (`192.168.x.x`) that the internet cannot reach. Drishti
works with all of them in four ways. Pick the first one unless you have a reason not to.

| | How it works | Open ports / VPN needed | Camera password leaves the site | Works behind CGNAT / hotel / hospital Wi-Fi | Best for |
|---|---|---|---|---|---|
| **1. Edge agent** (recommended) | A tiny container at the site reads the camera locally and **pushes** pictures to Drishti over HTTPS | none | **no** | yes | everything, especially hospitals and shared networks |
| **2. VPN** (Tailscale / WireGuard / OpenVPN) | Site and Drishti join one private network; the usual `rtsp://192.168...` URL then just works | the VPN | yes (inside the VPN) | Tailscale yes | Drishti on your laptop, you control both ends |
| **3. Tunnel** (ngrok TCP) | A public `tcp://host:port` forwards to the camera | none, but a public address exists | yes (and the provider carries it) | yes | quick demos |
| **4. Router port-forward** | Open the camera's port on the router | **yes** | yes | no | **don't** (see below) |

Drishti only needs a picture every 30-120 s (an unchanged scene is not even sent to a model), so all of these are light on bandwidth.

---

## 1. Edge agent (recommended)

```
 camera 192.168.1.50 --RTSP, local--> [ camera-agent ] --HTTPS, outbound only--> Drishti gateway
```

1. In the dashboard open **Admin > pick the site > Connect a camera > Edge agent > PAIR A SITE**. It shows a token **once**.
   The token can only send pictures for that one site: it cannot read alerts, change settings or create more tokens, and you can revoke it
   (or API: `POST /tenants/{id}/edge-tokens`, `DELETE /tenants/{id}/edge-tokens/{token_id}`).
2. On any computer at the site (laptop, mini PC, Raspberry Pi 5) that can reach the camera:

   ```bash
   git clone https://github.com/dhirajpatra/Drishti-Cloud && cd Drishti-Cloud/edge/camera-agent
   cp .env.example .env      # paste GATEWAY_URL, TENANT_ID, EDGE_TOKEN and your CAMERAS
   docker compose run --rm camera-agent python agent.py --check   # reads one picture per camera, prints OK / FAIL
   docker compose up -d --build
   ```
3. Back in Admin the site turns **connected** and the camera appears in the live view. Several cameras at one site: list them all in `CAMERAS=`.

`GATEWAY_URL` must be reachable **from the site**:

* Drishti on a cloud server: `https://your-domain` (put TLS in front, e.g. Caddy / nginx / the cloud load balancer).
* Drishti on your laptop: expose only the gateway, e.g. `ngrok http 8000` (use the `https://...ngrok-free.app` it prints) or give both machines
  Tailscale and use `http://100.x.y.z:8000`.
* **Whenever the gateway is reachable beyond your own LAN, set `AUTH_REQUIRED=true` and `ADMIN_TOKEN` in `.env`.** Site tokens keep working;
  everything else then needs a tenant key or the admin token.

What the agent does: reads a picture every `POLL_SEC`, compares it with the last one (32x24 brightness-normalised grid, like the server), sends a
changed scene at most every `INTERVAL_SEC` (a change that arrives too soon is **held and sent**, never dropped), and sends a keep-alive picture every
`KEEPALIVE_SEC` so the camera shows as online. A camera that is off or a link that drops is retried with growing pauses (up to 5 min) and logged once.
No motion sensor is needed: on a camera-only site, **a changing picture counts as motion**, so "no motion for N minutes" works without a PIR.

## 2. VPN

*Tailscale (easiest, free personal plan).* At the site run `deploy/remote/tailscale-site.compose.yml` (advertises the camera LAN), approve the route in the
Tailscale admin console, and install Tailscale on the machine that runs Drishti. The camera URL stays `rtsp://192.168.1.50:554/...`.
**Two sites that both use `192.168.1.0/24` collide**: use the edge agent for those, or the camera's own Tailscale address.

*WireGuard.* Run a WireGuard server on the Drishti side (a cloud VM is typical), put a WireGuard client on a small device at the site (many routers have one),
route the camera subnet through it, and use the camera's LAN address. Keep the tunnel's allowed IPs to the camera subnet only.

*OpenVPN.* Same idea as WireGuard (site-to-site with a server you run); Drishti needs nothing special as long as the server can open
`rtsp://<camera-ip>:554/...`. We document it rather than ship a server because OpenVPN needs a PKI and a public host: prefer WireGuard or Tailscale if you can.

On a shared cloud server set `ALLOW_PRIVATE_CAMERA_HOSTS=false` so tenants cannot aim the ingestor at the server's own private network; VPN addresses
(`100.64.0.0/10`, Tailscale) and public names stay allowed.

## 3. ngrok TCP tunnel (demo)

At the site: `deploy/remote/ngrok-site.compose.yml` (set `NGROK_AUTHTOKEN`, `CAMERA_HOST`, `CAMERA_PORT`). ngrok prints `tcp://0.tcp.in.ngrok.io:12345`.
In **Admin > camera** choose protocol `rtsp` (or paste `tcp://...`: Drishti reads it as RTSP), host `0.tcp.in.ngrok.io`, port `12345`, your usual path, username
and password, then **TEST CONNECTION**.

Honest limits: free TCP tunnels may require a verified ngrok account; the host and port **change every time ngrok restarts** (update them in Admin: no `.env`
edit or restart needed); the tunnel provider carries the video; and anyone who learns the address can try to log in to your camera, so use a strong camera password.

## 4. Router port-forward: please don't

Cameras exposed to the internet are scanned and attacked within hours (default passwords, old firmware). If you must, forward a **non-standard external port**,
disable UPnP, use a long unique password, and update the firmware. The edge agent or a VPN gives the same result with no exposed port.

---

## Finding the camera's RTSP address

| Camera | Typical URL |
|---|---|
| Tapo / TP-Link | `rtsp://user:pass@IP:554/stream1` (`stream2` = low quality) |
| Hikvision | `rtsp://user:pass@IP:554/Streaming/Channels/101` |
| Dahua / Amcrest | `rtsp://user:pass@IP:554/cam/realmonitor?channel=1&subtype=0` |
| motionEye (Raspberry Pi) | stream `http://IP:8081/` or picture `http://IP:80/picture/1/current/` |
| ESP32-CAM | `http://IP/capture` |

Test any URL in VLC first (*Media > Open Network Stream*). If VLC cannot play it, Drishti cannot either.

## TEST CONNECTION tells you which step failed

Admin > camera > **TEST CONNECTION** runs `dns -> tcp -> rtsp -> frame` and shows the first step that fails with a hint:

| Failing step | Meaning | Try |
|---|---|---|
| `host` | the address is not allowed (loopback, Drishti's own services, cloud metadata, or a private LAN IP on a shared server) | use the real camera address, or the edge agent / VPN / tunnel |
| `dns` | the name cannot be found | use the IP; check the VPN or tunnel is up |
| `tcp` timeout | no route from the Drishti server to that address | a `192.168.x.x` camera is unreachable from the cloud: edge agent / VPN / tunnel |
| `tcp` refused | reachable, but nothing listens | wrong port, camera's RTSP switched off, or the ngrok address changed |
| `rtsp` | the port is open but is not RTSP, or the path is wrong (404) | check the port and the stream path |
| `frame` | connected but no picture | wrong username / password, or the camera's stream limit is used up by another viewer |

## Security notes

* Camera passwords you enter in Admin are stored **encrypted** (Fernet; set `SECRETS_KEY`) and never returned by the API; logs and errors show `//***@`.
* A camera host may never be loopback, link-local (cloud metadata `169.254.169.254`) or one of Drishti's own services (`redis`, `postgres`, `neo4j`, `gateway`...),
  checked when you save **and** again after DNS resolution right before connecting.
* Site tokens are stored as SHA-256 hashes, shown once, scoped to one tenant and one route (frame ingest), revocable, and the dashboard shows when each last sent a picture.
* The edge agent mode keeps the camera password on the camera network: Drishti never sees it.
