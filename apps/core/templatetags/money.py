from django import template

from apps.core.money import format_cents

register = template.Library()


@register.filter
def cents(value):
    return format_cents(value)
