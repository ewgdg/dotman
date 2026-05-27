# Doctor Diagnostics

## Intent

Summarize dependency, repository, tracked-state, and snapshot health.

## Behavior

```pseudo
doctor_engine(engine):
  checks = dependency checks
  checks += repo checks for every configured repo
  checks += tracked-package entry checks
  checks += snapshot storage checks
  return DoctorSummary(checks)

DoctorSummary.ok():
  if any check has failed status:
    return false
  return true

DoctorSummary.failed_checks():
  return checks with failed status

DoctorSummary.warning_checks():
  return checks with warning status

DoctorCheck.to_dict():
  return status, category, label, detail, and remediation fields
```
