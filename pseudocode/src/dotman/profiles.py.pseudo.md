# Profile Ranking

## Intent

Order profiles so inherited parents are processed before child profiles.

## Behavior

```pseudo
compute_profile_heights(profiles):
  for each profile:
    follow extends chain to ancestors

    if ancestor profile is missing:
      reject profile graph

    if extends chain cycles:
      reject profile graph

    height = number of ancestor levels
    record profile height

  return heights by profile id

rank_profiles(profiles):
  heights = compute_profile_heights(profiles)
  sort profiles by height first, then profile id for stable ties
  return sorted profiles
```

## Review Needed

Exact missing-parent and cycle error behavior should be verified before changes.
