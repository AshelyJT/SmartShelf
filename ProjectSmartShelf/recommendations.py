import datetime
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from .models import DailyProductSales, PriceHistory
from .ml_models.utils import predict_prices_batch


AI_PRICE_REASON_MARKERS = (
    'Applied AI pricing recommendation',
    'Applied all AI pricing recommendations',
    'AI suggests',
)
AI_RECOMMENDATION_MIN_DIFF_PERCENT = Decimal('5')


def price_difference_percent(current_price, ai_price):
    if current_price <= 0:
        return Decimal('0')
    return ((ai_price - current_price) / current_price) * Decimal('100')


def has_recent_ai_price_application(product, applied_date=None):
    if applied_date is None:
        applied_date = timezone.localdate()

    marker_query = Q()
    for marker in AI_PRICE_REASON_MARKERS:
        marker_query |= Q(reason__icontains=marker)

    return PriceHistory.objects.filter(
        product=product,
        changed_at__date=applied_date,
    ).filter(marker_query).exists()


def should_suppress_ai_recommendation(product, current_price, ai_price, applied_date=None):
    price_diff_pct = abs(price_difference_percent(current_price, ai_price))
    if price_diff_pct >= AI_RECOMMENDATION_MIN_DIFF_PERCENT:
        return False
    return has_recent_ai_price_application(product, applied_date=applied_date)


def build_recommendations(base_date):
    raw_results = predict_prices_batch(base_date=base_date)
    recommendations = []

    for product, raw_ai_price in raw_results:
        current_price = product.price if product.price else Decimal('0.00')
        ai_price = Decimal(str(raw_ai_price)).quantize(Decimal('0.01'))
        expiry_date = product.expiry_date
        days_until_expiry = None
        if expiry_date:
            days_until_expiry = (expiry_date - base_date).days

        rec_type = None
        reason = ''
        urgency = 'low'

        if days_until_expiry is not None and days_until_expiry < 0:
            ai_price = Decimal('0.00')

        if (
            (days_until_expiry is None or days_until_expiry >= 0)
            and should_suppress_ai_recommendation(product, current_price, ai_price)
        ):
            continue

        price_diff_pct = price_difference_percent(current_price, ai_price)

        if days_until_expiry is not None and days_until_expiry < 0:
            urgency = 'high'
            rec_type = 'clearance'
            reason = 'Expired - product should not be sold'
        elif days_until_expiry is not None and days_until_expiry <= 7:
            if days_until_expiry <= 2:
                urgency = 'high'
                rec_type = 'clearance'
                reason = f'Expires in {days_until_expiry} days - urgent markdown'
            else:
                urgency = 'medium'
                rec_type = 'markdown'
                reason = f'Expires in {days_until_expiry} days - suggest discount'
        elif ai_price > current_price and price_diff_pct > 5:
            rec_type = 'price_increase'
            reason = f'AI suggests +{price_diff_pct:.0f}% price increase'
            urgency = 'medium'
        elif ai_price < current_price and price_diff_pct < -5:
            rec_type = 'price_decrease'
            reason = f'AI suggests {price_diff_pct:.0f}% price decrease'
            urgency = 'medium'
        else:
            recent_sales = DailyProductSales.objects.filter(
                product_name=product.name,
                date__gte=base_date - datetime.timedelta(days=7),
            ).aggregate(total=Sum('units_sold'))['total'] or 0
            if recent_sales < 5 and current_price > 0:
                rec_type = 'restock'
                reason = f'Low sales velocity ({recent_sales} units in 7 days) - review stock'
                urgency = 'low'
            else:
                continue

        recommendations.append({
            'product': product,
            'current_price': current_price,
            'ai_price': ai_price,
            'days_until_expiry': days_until_expiry,
            'rec_type': rec_type,
            'reason': reason,
            'urgency': urgency,
        })

    urgency_order = {'high': 0, 'medium': 1, 'low': 2}
    recommendations.sort(key=lambda x: urgency_order.get(x['urgency'], 3))
    return recommendations


def recommendation_count(base_date):
    return len(build_recommendations(base_date))
