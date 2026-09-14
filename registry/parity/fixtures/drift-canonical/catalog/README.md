# Canonical CMR catalog (DELIBERATE DRIFT fixture)

Same as `../../canonical/catalog/` but paired with the drift role schema one
level up, which declares an extra canonical role the registry has not
backfilled. The parity gate must be NOT-OK (exit 1) against this fixture.
