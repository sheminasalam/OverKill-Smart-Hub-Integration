# Publishing this integration repository

This package is prepared for a standalone repository named `OverKill-Smart-Hub-Integration`, separate from the firmware/hardware repository.

## 1. Create the public GitHub repository

On GitHub, create an **empty public repository** named `OverKill-Smart-Hub-Integration` under `sheminasalam`. Do not initialize it with a README, license or `.gitignore`; those files are already in this package. Enable Issues, and set the repository description and topics as listed in `PRE_RELEASE_CHECKLIST.md`.

## 2. Push this package

Extract `OverKill-Smart-Hub-Integration-Repository-v1.3.0-rc1.zip` into a temporary folder. Clone the newly created empty repository, then copy the *contents* of the extracted package into the clone root (including the hidden `.github` directory). From the clone, run:

```bash
git add .
git commit -m "feat: prepare OverKill Smart Hub HACS integration v1.3.0"
git push -u origin main
```

If the repository's default branch is named differently, push to that branch and make it the default in GitHub settings. Do not add an extra `OverKill-Smart-Hub-Integration-Repository/` wrapper directory; `hacs.json` and `custom_components/` must be at the repository root.

## 3. Read the first CI results

Open the repository's **Actions** tab. Confirm Static checks, HACS validation and Hassfest complete. The HACS action may report a Brands requirement until the separate Home Assistant Brands PR is merged. Do not hide or ignore a failing check for a default-store submission; fix the underlying issue.

## 4. Choose a license and submit branding

Before announcing a public release, replace `LICENSE_PENDING.md` with the license chosen by the project owner. The Home Assistant Brands contribution payload is provided separately as `OverKill-HomeAssistant-Brands-Submission.zip`; open a PR to `home-assistant/brands` adding its `custom_integrations/overkill_smart_hub/` directory.

## 5. Test, release, then optionally submit to HACS default

Test the integration in a clean Home Assistant instance using `PRE_RELEASE_CHECKLIST.md`. Once the HACS and Hassfest workflows pass, the license is resolved and branding is registered, create a full GitHub Release tagged `v1.3.0`. Only after these gates are met should you submit a PR to `hacs/default` for default-store inclusion.
