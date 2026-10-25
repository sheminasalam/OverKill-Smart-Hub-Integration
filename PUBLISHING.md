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

Open the repository's **Actions** tab. Confirm Static checks, Custom HACS compatibility checks and Hassfest complete. This project uses PolyForm Noncommercial to reserve commercial use for separate permission, so it is not eligible for the HACS default list. The Custom HACS compatibility workflow checks the repository layout and chosen license for the supported custom-repository path; it does not claim default-store eligibility.

## 4. Choose a license and submit branding

The repository uses PolyForm Noncommercial License 1.0.0, which restricts commercial use. The Home Assistant Brands contribution payload is provided separately as `OverKill-HomeAssistant-Brands-Submission.zip`; open a PR to `home-assistant/brands` adding its `custom_integrations/overkill_smart_hub/` directory.

## 5. Test, release, then optionally submit to HACS default

Test the integration in a clean Home Assistant instance using `PRE_RELEASE_CHECKLIST.md`. Once the custom-repository checks and Hassfest pass, the release has been tested and branding is registered, create a full GitHub Release tagged `v1.3.0`. Do not submit this non-commercially licensed repository to `hacs/default`; the license is intentionally incompatible with its open-source license requirement.
