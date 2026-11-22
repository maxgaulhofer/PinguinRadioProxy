# Pinguin Radio Proxy

A small Flask service that proxies the Pinguin Radio audio stream and exposes now-playing metadata and album artwork. It runs in a container and can be deployed to a Kubernetes cluster, including k3s.

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

With the included MetalLB Service, the app is exposed on port `80` at the configured LoadBalancer IP. For example: `http://<load-balancer-ip>/metadata` and `http://<load-balancer-ip>/radio`. The address pool and optional requested IP are set in [`k8s/svc.yaml`](k8s/svc.yaml).

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `TRACK_METADATA_DELAY_SECONDS` | `10` | Seconds to wait before publishing a detected track change. Use `0` for no delay. |
| `PUBLIC_BASE_URL` | Automatically detected | Optional public base URL used when generating cover-art links, for example `http://<load-balancer-ip>`. |

The delay is configured in [`k8s/deployment.yaml`](k8s/deployment.yaml).

## Build the container image

Run these commands from the project directory on a machine with Docker Buildx. Build for the architecture used by the Kubernetes cluster nodes. For ARM64 nodes:

```sh
docker buildx build --platform linux/arm64 --load -t pinguin-radio-proxy:latest .
docker save pinguin-radio-proxy:latest -o pinguin-radio-proxy.tar
```

For AMD64 nodes, replace `linux/arm64` with `linux/amd64`. For 32-bit ARM nodes, use `linux/arm/v7`.

The **Build and publish container image** workflow builds and publishes the ARM64 image to GitHub Container Registry (GHCR). Start it from the **Actions** tab using **Run workflow**. It publishes `latest` when run from the repository's default branch, along with a branch tag and a `sha-...` tag.

## Deploy from GitHub Actions

The **Deploy to k3s** workflow deploys an image tag to the cluster. It runs on the self-hosted runner labeled `k3s-org-runner` and expects that runner to have `kubectl` installed and configured to access the target cluster.

In the repository's **Actions** tab, select **Deploy to k3s**, choose **Run workflow**, and enter the GHCR image tag to deploy (for example, `latest` or a `sha-...` tag). The workflow applies the manifests in `k8s/` and waits for the Deployment rollout.

The cluster must be able to pull the image from GHCR. Make the GHCR package public, or configure an image pull secret in the cluster if the package is private.

Copy the image archive to a machine that can access the Kubernetes cluster. Replace the example SSH target with the appropriate account and host:

```sh
scp pinguin-radio-proxy.tar <user>@<cluster-host>:~/
```

## Deploy to a k3s cluster

If the cluster uses k3s, import the image into containerd on the node that will run the workload:

```sh
ssh <user>@<cluster-node>
sudo k3s ctr --namespace k8s.io images import ~/pinguin-radio-proxy.tar
```

Apply the Deployment and MetalLB Service manifests from this repository using `kubectl` configured for your cluster:

```sh
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/svc.yaml
```

Check the rollout and allocated external IP:

```sh
kubectl rollout status deployment/pinguin-radio-proxy
kubectl get service pinguin-radio-proxy
```

The Service uses the MetalLB pool named `doc-example`. Set the pool name and, optionally, a requested IP in [`k8s/svc.yaml`](k8s/svc.yaml) to match your cluster. Once MetalLB assigns an address, use `http://<load-balancer-ip>/radio` as the stream URL. Other endpoints are available at the same address, such as `http://<load-balancer-ip>/api/nowplaying`.

## Update the deployment

After changing the app, build and save the image again, then copy it to a cluster node:

```sh
docker buildx build --platform linux/arm64 --load -t pinguin-radio-proxy:latest .
docker save pinguin-radio-proxy:latest -o pinguin-radio-proxy.tar
scp pinguin-radio-proxy.tar <user>@<cluster-node>:~/
```

On the node, import the replacement image and restart the Deployment:

```sh
k3s ctr --namespace k8s.io images import ~/pinguin-radio-proxy.tar
kubectl rollout restart deployment/pinguin-radio-proxy
kubectl rollout status deployment/pinguin-radio-proxy
```

The Deployment uses `imagePullPolicy: Never`, so k3s uses the image imported locally. View application logs with:

```sh
kubectl logs deployment/pinguin-radio-proxy
```
