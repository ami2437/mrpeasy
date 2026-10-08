#!/bin/bash
# The AT-HUB instances on this server (sourced by the other athub-* scripts).
#   prod  /opt/athub       service athub       127.0.0.1:8010   https://americantradershub.com
#   test  /opt/athub-test  service athub-test  127.0.0.1:8011   https://test.americantradershub.com
instance() {
  case "$1" in
    prod) DIR=/opt/athub;      SVC=athub;      PORT=8010; DOMAIN=americantradershub.com ;;
    test) DIR=/opt/athub-test; SVC=athub-test; PORT=8011; DOMAIN=test.americantradershub.com ;;
    *) echo "unknown instance '$1' (test or prod)" >&2; exit 2 ;;
  esac
}
