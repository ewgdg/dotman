# Elevation Broker

## Intent

Let child processes request privileged operations through a temporary broker owned by the parent process.

## Behavior

```pseudo
elevation_broker_session(reason):
  start broker
  expose broker environment variables inside context
  on context exit, close active broker

ElevationBroker.start():
  create local socket
  start serving requests in background

_handle_connection(connection):
  validate peer identity

  if peer is invalid:
    return error response

  read JSON request payload

  if payload is malformed or unsupported:
    return error response

  run requested elevated operation
  return JSON result payload

request_elevation_from_env(reason):
  if broker environment is absent:
    report no broker available

  connect to broker
  send elevation request
  return broker response
```

## Review Needed

Peer validation, request schema, and privileged operation dispatch are security-sensitive; review implementation before changes.
