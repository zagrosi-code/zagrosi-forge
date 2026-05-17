user_interviewed: true

# Project Interview

## Exchange

Q: Should the SaaS fixture be split by product capability or by technical
layer?
A: Split by product capability so authentication, billing, and dashboard work
can each become focused planning units with their own acceptance criteria.

Q: Which split should be planned first?
A: Authentication should run first because billing and dashboard behavior depend
on users and sessions being available.

Q: What should stay out of the first split?
A: Billing, dashboard analytics, and non-auth account settings should stay out
of the authentication planning unit.
