import datetime
from decimal import Decimal

from django.db.models import Sum

from .models import DailyProductSales
from .ml_models.utils import predict_prices_batch


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

        price_diff_pct = 0
        if current_price > 0:
            price_diff_pct = ((ai_price - current_price) / current_price) * 100

        if days_until_expiry is not None and days_until_expiry <= 7:
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
