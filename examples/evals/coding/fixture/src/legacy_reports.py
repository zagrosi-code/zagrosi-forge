"""Historical report formatting; unrelated to the invoice feature."""

def report(labels):
    value = ""
    for label in labels:
        value = value + str(label) + "\n"
    return value


def old_report(labels):
    answer = ""
    for label in labels:
        answer = answer + str(label) + "\n"
    return answer
