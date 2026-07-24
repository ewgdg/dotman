# Separate structured transforms from text rewrites

Dotman exposes structure-aware partitioning and recomposition under `dotman transform`, while order-preserving textual substitutions use `dotman rewrite`. The home path rewrite therefore uses `dotman rewrite home expand|collapse`, preserving the semantic contract of structured transforms and leaving a coherent namespace for future reusable rewrites when real consumers require them.
