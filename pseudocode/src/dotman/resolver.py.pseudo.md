# Query Resolution Ranking

## Intent

Build searchable fields and rank user queries against selectors, packages, targets, and profiles.

## Behavior

```pseudo
rank_resolver_option(query, option):
  if query is empty:
    return no match unless caller allows empty listing

  for each searchable field on option:
    compute field match rank:
      exact normalized match beats prefix match
      prefix match beats segment match
      segment match beats substring match
      no textual relation is no match

    combine field match rank with field kind priority

  if no field matches:
    return no match

  return best rank for option

build_*_match_fields(object):
  include primary identity field
  include useful aliases or component fields
  include repo/package/profile/target fields only when meaningful for that object type

parse_slash_qualified_query(text):
  split query into slash-qualified segments
  preserve segment meaning for repo/package/profile/target searches
  reject or normalize unsupported segment shapes according to caller needs

build_fzf_search_fields(option):
  return human label plus hidden search fields useful for fzf matching
```

## Review Needed

Ranking constants, field priorities, and tie-break behavior need implementation review before changes.
