# Google Maps links

Source: Google Maps Platform, "Get started with Maps URLs", https://developers.google.com/maps/documentation/urls/get-started (checked 2026-11-28). The links are keyless web links. On a phone with Google Maps installed they open in the app.

Every link needs `api=1`; without it the other parameters are ignored. URL-encode values: `%2C` for a comma, `%7C` for a pipe, `+` or `%20` for a space.

| Purpose | Pattern |
| --- | --- |
| Route to a destination | `https://www.google.com/maps/dir/?api=1&destination={place}` |
| Route with a mode | add `&travelmode={mode}` |
| Route from A to B | add `&origin={place}` |
| Route to a place id | add `&destination_place_id={placeId}` (keep `destination` as the name) |
| Show or search a place | `https://www.google.com/maps/search/?api=1&query={place}` (add `&query_place_id={placeId}` when known) |

- `{place}` is a name, an address or `lat,lng` in decimal degrees.
- `{mode}` is one of `driving`, `walking`, `bicycling`, `two-wheeler` and `transit`. Without it Google picks the likeliest mode.
- Without `origin`, Google uses the device's current position where it can. That is usually what the owner wants on the move.

Example from the guide (bicycle):
`https://www.google.com/maps/dir/?api=1&origin=Space+Needle+Seattle+WA&destination=Pike+Place+Market+Seattle+WA&travelmode=bicycling`
