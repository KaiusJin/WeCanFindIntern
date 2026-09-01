#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /path/to/WeCanFindIntern.app" >&2
  exit 2
fi

app_path="$1"
if [[ ! -d "$app_path" ]]; then
  echo "macOS app bundle does not exist: $app_path" >&2
  exit 1
fi

codesign --verify --deep --strict --verbose=2 "$app_path"
signature_details="$(codesign --display --verbose=3 "$app_path" 2>&1)"

if ! grep -Fq "Identifier=com.wecanfindintern.desktop" <<<"$signature_details"; then
  echo "Unexpected or missing macOS bundle identifier:" >&2
  echo "$signature_details" >&2
  exit 1
fi

if ! grep -Fq "Signature=adhoc" <<<"$signature_details"; then
  echo "Expected an ad-hoc signature for the certificate-free macOS build:" >&2
  echo "$signature_details" >&2
  exit 1
fi

echo "Validated complete ad-hoc signature: $app_path"
