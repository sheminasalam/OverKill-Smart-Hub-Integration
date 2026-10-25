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

Open the repository's **Actions** tab. Confirm Static checks, the official HACS Action and Hassfest complete successfully without ignored failures. The lightweight package check supplements HACS validation.

## 4. License and Home Assistant Brands

The repository now uses the same MIT License as the Net Metering integration. MIT permits commercial and non-commercial use, subject to its notice and warranty terms. The Home Assistant Brands contribution payload is provided separately as `OverKill-HomeAssistant-Brands-Submission.zip`; open a PR to `home-assistant/brands` adding its `custom_integrations/overkill_smart_hub/` directory.

## 5. Test, release, then optionally submit to HACS default

Test the integration in a clean Home Assistant instance using `PRE_RELEASE_CHECKLIST.md`. Once the official HACS Action and Hassfest pass, the functional test matrix is complete, and branding is registered, create a full GitHub Release tagged `v1.3.0`. Then submit the integration to your `hacs/default` fork on a new branch based on `master`, and open a PR against `hacs/default`.
