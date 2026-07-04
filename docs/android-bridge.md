# Android Bridge

Talk to your assistant from your phone, anywhere on your local network.

The Android app is a separate module:

- Repo: [systema-auxilium-android-module](https://github.com/uukjtisa/systema-auxilium-android-module)
- Releases: [download](https://github.com/uukjtisa/systema-auxilium-android-module/releases)

## Enable it

1. In the desktop app, open **Settings → System → Android Packet** and enable the
   bridge.
2. Note the `IP:port` shown for your machine on the LAN.
3. In the phone app, connect using that `IP:port` over Wi-Fi.

## What it does

Once connected, the phone mirrors your assistant over the LAN — you can chat from
the couch or another room while the app keeps running on your computer.

The bridge also mirrors the [code-approval flow](security.md): when the assistant
wants to run code and Supervised Execution asks for approval, the phone is
notified and can approve or reject, keeping you in control even when you are away
from the desk.

> The bridge runs on your **local network** only. Keep it on a trusted network;
> anyone who can reach the `IP:port` can talk to your assistant.
