# FDOF Server prototypes

In this repository we have the prototypes of FDOF server implementation. In these prototypes we tried four different approaches:
- HTTP Signposting (with custom link rel types)
- Custom HTTP methods (GETIR, GETMETADATA and GETTYPE)
- HTTP Accept Header with custom Media types (application/fdof-ir+trig, application/fdof-metadata+trig, application/type+ttl)
- Custom REST API paths (/{id}/identifierRecord, /{id}/metadataRecord, /{id}/type)

The purpose of these prototypes were to investigate the options for implementing the expected FDOF behaviours and the feasibility related to the developer perspective.
