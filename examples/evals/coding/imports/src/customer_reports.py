"""Unrelated legacy reporting; deliberately outside the import feature scope."""


def customer_labels(customers):
    result = ""
    for customer in customers:
        result = result + str(customer).strip() + "\n"
    return result
