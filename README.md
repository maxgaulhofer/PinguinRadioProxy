# Pinguin Radio Proxy

A small Flask service that proxies the Pinguin Radio audio stream and exposes now-playing metadata and album artwork. It is designed to run in a container on a Raspberry Pi, including clusters managed with k3s.

## Features

- Proxies the live MP3 stream at `/radio`.
- Reads ICY stream metadata and republishes it for compatible players.
- Provides JSON now-playing endpoints and cover art.
- Delays track changes by 10 seconds by default, helping keep displayed track info in sync with playback.
- Runs with Gunicorn in a non-root container.

## HTTP endpoints

| Path | Description |
| --- | --- |
| `/radio` | Live MP3 stream |
| `/metadata` | Current station and track metadata as JSON |
| `/api/nowplaying` | Current artist, title, stream title, and cover URL as JSON |
| `/cover.jpg` | Current album art, when available |
| `/` | Simple status page |

With the included MetalLB Service, the app is exposed on port `80` at the configured LoadBalancer IP. For example: `http://192.168.2.231/metadata` and `http://192.168.2.231/radio`. The IP and address pool are set in [`k8s/svc.yaml`](k8s/svc.yaml).

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `TRACK_METADATA_DELAY_SECONDS` | `10` | Seconds to wait before publishing a detected track change. Use `0` for no delay. |
| `PUBLIC_BASE_URL` | Automatically detected | Optional public base URL used when generating cover-art links, for example `http://192.168.2.231`. |

The delay is configured in [`k8s/deployment.yaml`](k8s/deployment.yaml).

## Build for Raspberry Pi

Run these commands from the project directory on a machine with Docker Buildx. For a 64-bit Raspberry Pi OS, build an ARM64 image:

```sh
docker buildx build --platform linux/arm64 --load -t pinguin-radio-proxy:latest .
docker save pinguin-radio-proxy:latest -o pinguin-radio-proxy.tar
```

For a 32-bit Raspberry Pi OS, replace `linux/arm64` with `linux/arm/v7`.

Copy the image archive to the Pi. Replace the example SSH target with your Pi's username and hostname or IP:

```sh
scp pinguin-radio-proxy.tar pi@raspberrypi.local:~/
```

## Deploy with k3s

SSH into the Pi and import the image into k3s's containerd image store:

```sh
ssh pi@raspberrypi.local
sudo k3s ctr --namespace k8s.io images import ~/pinguin-radio-proxy.tar
```

Apply the Deployment and MetalLB Service manifests from this repository. If the repository is not on the Pi, copy the `k8s/` directory there first.

```sh
sudo k3s kubectl apply -f k8s/deployment.yaml
sudo k3s kubectl apply -f k8s/svc.yaml
```

Check the rollout and allocated external IP:

```sh
sudo k3s kubectl rollout status deployment/pinguin-radio-proxy
sudo k3s kubectl get service pinguin-radio-proxy
```

The Service uses the MetalLB pool named `doc-example` and requests `192.168.2.231` in [`k8s/svc.yaml`](k8s/svc.yaml). Change those values to match your network if needed. Once the address is assigned, use `http://<load-balancer-ip>/radio` as the stream URL. The other endpoints are available at the same address, such as `http://<load-balancer-ip>/api/nowplaying`.

## Update the deployment

After changing the app, build and save the image again, then copy it to the Pi:

```sh
docker buildx build --platform linux/arm64 --load -t pinguin-radio-proxy:latest .
docker save pinguin-radio-proxy:latest -o pinguin-radio-proxy.tar
scp pinguin-radio-proxy.tar pi@raspberrypi.local:~/
```

On the Pi, import the replacement image and restart the Deployment:

```sh
sudo k3s ctr --namespace k8s.io images import ~/pinguin-radio-proxy.tar
sudo k3s kubectl rollout restart deployment/pinguin-radio-proxy
sudo k3s kubectl rollout status deployment/pinguin-radio-proxy
```

The Deployment uses `imagePullPolicy: Never`, so k3s uses the image imported locally. View application logs with:

```sh
sudo k3s kubectl logs deployment/pinguin-radio-proxy
```
