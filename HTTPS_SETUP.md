# Setting up HTTPS (needed for offline sync on the tablet)

This is a one-time setup (see PROJECT_NOTES.md for why it's needed - short version:
Service Workers, which offline sync depends on, only work over HTTPS on any device
other than the PC itself). Takes about 30-45 minutes. You'll do steps 1-4 on your PC,
then step 5 on your tablet.

**You already have step 0 done** (a fixed/reserved LAN IP for this PC in your router) -
that's what makes the certificate stay valid long-term rather than breaking the next
time your router hands this PC a different IP address.

---

## Step 1: Find this PC's LAN IP address

Open Command Prompt and run:
```
ipconfig
```
Look for "IPv4 Address" under your active network adapter (Wi-Fi or Ethernet) - it'll
look like `192.168.1.50` or similar. **Write this down** - you'll need it in step 3.
This should match the fixed/reserved IP you already set up in your router.

## Step 2: Install mkcert

mkcert is a small, well-established tool that creates a private certificate authority
just for your own devices (nothing gets sent anywhere, nothing public is involved).

If you don't already have Chocolatey (a Windows package manager) installed, install it
first from **https://chocolatey.org/install** (their site has a one-line PowerShell
command to run as Administrator).

Then, in an **Administrator** PowerShell or Command Prompt:
```
choco install mkcert -y
```

Confirm it installed:
```
mkcert --version
```

## Step 3: Create the local certificate authority and generate the certificate

Still in that Administrator terminal:
```
mkcert -install
```
This creates a local certificate authority on your PC and trusts it in Windows and any
browsers already installed. You'll see a message confirming it worked.

Now generate the actual certificate - **replace `192.168.1.50` below with the real LAN
IP you found in step 1**, and replace the path with wherever your actual "TSW Hud"
project folder is:
```
cd "C:\path\to\your\TSW Hud"
mkcert -cert-file certs\cert.pem -key-file certs\key.pem 192.168.1.50 localhost 127.0.0.1
```
This creates `certs\cert.pem` and `certs\key.pem` directly where `app.py` already
expects to find them - no renaming needed. The moment these two files exist, the next
time you start the app it'll automatically switch to HTTPS - nothing else to configure.

## Step 4: Confirm it worked

Start the app as normal (`run.bat`). The console window should now print something like:
```
HTTPS enabled - certificate found in certs/. Reachable at https://<this PC's LAN IP>:<port>
```
instead of the old plain-HTTP message. Open `https://192.168.1.50:<port>` (your real IP)
in a browser **on this same PC** first - it should load with no security warning at all
(since this PC already trusts the certificate authority from step 3).

## Step 5: Trust the certificate authority on your tablet

Your tablet doesn't know about the certificate authority you just created on your PC, so
it needs to be told to trust it too - this is the one manual step per device.

1. **Find the root certificate file on your PC.** Run this in a terminal:
   ```
   mkcert -CAROOT
   ```
   This prints a folder path. Inside it is a file called `rootCA.pem`.
2. **Get that `rootCA.pem` file onto your tablet** - easiest is emailing it to yourself,
   or using a cloud drive (Google Drive/OneDrive) synced to both devices, or a USB cable.
3. **On the tablet**, open **Settings → Security & privacy → More security settings →
   Encryption & credentials → Install a certificate → CA certificate** (exact wording
   varies slightly by Android version/Samsung's skin, but it's under Security settings).
   Android will show a warning about trusting a certificate authority - that's expected
   and correct for what we're doing here, since this is a private CA you created
   yourself for your own devices, not something from an unknown third party.
4. Select the `rootCA.pem` file you transferred over.
5. **You'll now see a small persistent notification** ("Network may be monitored" or
   similar) any time this profile is active - this is Android's standard, generic
   warning for *any* manually-installed certificate authority, security-conscious by
   design. It's expected and not a sign anything's wrong.

## Step 6: Test from the tablet

With the PC running the app (now over HTTPS) and the tablet on the same Wi-Fi, open
`https://192.168.1.50:<port>` (your real PC IP) in Chrome on the tablet. It should load
with no certificate warning. If you see a warning, double check the IP address in the
certificate (step 3) matches this PC's actual current LAN IP exactly.

---

## Notes for later

- **This is mostly one-time.** The certificate mkcert generates is valid for about 2
  years - after that you'd re-run step 3 to generate a fresh one (steps 1, 2, and 5
  wouldn't need repeating unless something changes).
- **If your PC's LAN IP ever changes** (shouldn't happen since you already have a fixed
  reservation in your router), the certificate would need regenerating for the new IP -
  re-run step 3 with the new address.
- **Nothing here exposes anything to the internet.** This is purely for devices already
  on your home Wi-Fi network to trust the connection - the same LAN-only reachability
  the app already had, just now over a trusted HTTPS connection instead of plain HTTP.
