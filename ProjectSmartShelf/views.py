import csv
import datetime
from datetime import timedelta
import json
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Sum, Q
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time
from checkout.models import Product
from decimal import Decimal
from .models import DailyProductSales, PriceHistory, ScheduledPromotion
from .apps import ProjectsmartshelfConfig
from .ml_models.utils import prepare_input_for_tomorrow, predict_prices_batch
from .recommendations import build_recommendations
from .simulation import (
    clear_active_simulated_date,
    get_active_day,
    get_active_simulated_date,
    set_active_simulated_date,
)
from .utils import send_price_update


AVAILABLE_LCD_IDS = (1, 2, 3)
PROMOTION_LOOKUP = object()


def process_promotions_for_request(request):
    """Process due promotions using the active simulated day when one exists."""
    return ScheduledPromotion.process_due_promotions(get_effective_promotion_time(request))


def get_effective_promotion_time(request):
    current_time = timezone.localtime()
    simulated_date = get_active_simulated_date(request)
    if simulated_date:
        effective_at = datetime.datetime.combine(
            simulated_date,
            current_time.time().replace(microsecond=0),
        )
        return timezone.make_aware(effective_at, timezone.get_current_timezone())
    return current_time.replace(microsecond=0)


def prepare_promotions_for_display(promotions, effective_at):
    prepared = list(promotions)
    for promotion in prepared:
        countdown_target = None
        if promotion.status == ScheduledPromotion.STATUS_ACTIVE:
            countdown_target = promotion.ends_at
            promotion.countdown_label = 'Ends in'
        elif promotion.status == ScheduledPromotion.STATUS_SCHEDULED:
            countdown_target = promotion.starts_at
            promotion.countdown_label = 'Starts in'
        else:
            promotion.countdown_label = 'Closed'

        promotion.countdown_target = countdown_target
        promotion.countdown_target_iso = countdown_target.isoformat() if countdown_target else ''
        if countdown_target:
            promotion.countdown_seconds = max(0, int((countdown_target - effective_at).total_seconds()))
        else:
            promotion.countdown_seconds = None
    return prepared


def get_active_promotions_by_product_id(product_ids=None):
    promotions = ScheduledPromotion.objects.filter(
        status=ScheduledPromotion.STATUS_ACTIVE
    ).select_related('product')
    if product_ids is not None:
        promotions = promotions.filter(product_id__in=product_ids)
    return {promotion.product_id: promotion for promotion in promotions}


def send_product_lcd_update(product, active_promotion=PROMOTION_LOOKUP):
    if active_promotion is PROMOTION_LOOKUP:
        active_promotion = ScheduledPromotion.objects.filter(
            product=product,
            status=ScheduledPromotion.STATUS_ACTIVE,
        ).first()

    if active_promotion:
        return send_price_update(
            product.lcd_id,
            product.name,
            str(product.price),
            promotion_label='PROMO',
        )
    return send_price_update(product.lcd_id, product.name, str(product.price))


def get_price_change_reason(request, fallback):
    reason = request.POST.get('price_change_reason', '').strip()
    return reason or fallback


def login_view(request):
    error_message = ""
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            if user.is_staff:
                login(request, user)
                next_url = (
                    request.POST.get("next") or
                    request.GET.get("next") or
                    reverse(settings.LOGIN_REDIRECT_URL)
                )
                return redirect(next_url)
            else:
                error_message = "You do not have staff privileges!"
        else:
            error_message = "Invalid username or password. Please try again."
    return render(request, "staff_login.html", {"error": error_message})

def staff_logout_view(request):
    logout(request)
    return redirect(reverse('staff_login'))

@staff_member_required(login_url='staff_login')
def analytics_dashboard(request):
    effective_at = get_effective_promotion_time(request)
    ScheduledPromotion.process_due_promotions(effective_at)
    simulated_today = get_active_day(request)
    
    yesterday = simulated_today - timedelta(days=1)
    
    recent_summaries = DailyProductSales.objects.all().order_by('-date')[:20]
    yesterday_top = DailyProductSales.objects.filter(date=yesterday).order_by('-units_sold')[:10]
    
    # Category data
    category_data = DailyProductSales.objects.filter(
        date=yesterday
    ).values('category').annotate(
        total_units=Sum('units_sold'),
        total_revenue=Sum('total_revenue')
    ).order_by('-total_units')
    
    # --- Top Products for Demand Trends (last 7 days) ---
    last_7_days = [simulated_today - timedelta(days=i) for i in range(7)]
    top_products_list = DailyProductSales.objects.filter(
        date__gte=simulated_today - timedelta(days=30)
    ).values('product_name', 'category').annotate(
        total_units=Sum('units_sold')
    ).order_by('-total_units')[:5]  # top 5 products by total units last 30 days
    
    top_products = []
    for prod in top_products_list:
        # Get daily sales for the last 7 days for this product
        daily_sales = DailyProductSales.objects.filter(
            product_name=prod['product_name'],
            date__gte=simulated_today - timedelta(days=7)
        ).order_by('date').values_list('units_sold', flat=True)
        
        # Convert to list and fill missing days with 0
        demand_data = list(daily_sales)
        while len(demand_data) < 7:
            demand_data.append(0)
        
        # Create date labels (last 7 days)
        date_labels = [(simulated_today - timedelta(days=i)).strftime('%m/%d') for i in range(6, -1, -1)]
        
        top_products.append({
            'name': prod['product_name'],
            'category': prod['category'],
            'demand_labels': date_labels,
            'demand_data': demand_data,
        })
    
    # Fallback if no data exists yet
    if not top_products:
        top_products.append({
            'name': 'Sample Product',
            'category': 'Sample',
            'demand_labels': ['Day 7', 'Day 6', 'Day 5', 'Day 4', 'Day 3', 'Day 2', 'Yesterday'],
            'demand_data': [12, 19, 3, 5, 2, 3, 9],
        })

    latest_promotions = prepare_promotions_for_display(
        ScheduledPromotion.objects.select_related('product', 'created_by').order_by('-created_at')[:4],
        effective_at,
    )
    
    context = {
        'today': simulated_today,
        'yesterday': yesterday,
        'recent_summaries': recent_summaries,
        'yesterday_top': yesterday_top,
        'category_data': category_data,
        'total_summaries': DailyProductSales.objects.count(),
        'top_products': top_products,   # <-- added for demand charts
        'latest_promotions': latest_promotions,
        'promotion_effective_at': effective_at,
    }
    return render(request, 'dashboard.html', context)


@staff_member_required(login_url='staff_login')
def generate_summary_api(request):
    """API endpoint to generate daily summary"""
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            target_date_str = data.get("date")

            if target_date_str:
                target_date = datetime.datetime.strptime(
                    target_date_str, "%Y-%m-%d"
                ).date()
            else:
                # Default to yesterday
                target_date = timezone.now().date() - datetime.timedelta(days=1)

            # Generate summary
            records = DailyProductSales.generate_daily_summary(target_date)

            return JsonResponse(
                {
                    "success": True,
                    "date": target_date_str or str(target_date),
                    "records_created": len(records),
                    "message": f"Generated {len(records)} daily summaries for {target_date}",
                }
            )

        except ValueError as e:
            return JsonResponse(
                {"success": False, "error": f"Invalid date format: {str(e)}"}
            )
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    return JsonResponse({"success": False, "error": "Invalid request method"})

@staff_member_required(login_url='staff_login')
def sales_report_api(request):
    """API endpoint to get sales data"""
    # Get date parameters
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")
    category = request.GET.get("category")
    product_name = request.GET.get("product_name")

    # Build queryset
    queryset = DailyProductSales.objects.all()

    # Apply filters
    if start_date_str:
        start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
        queryset = queryset.filter(date__gte=start_date)

    if end_date_str:
        end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
        queryset = queryset.filter(date__lte=end_date)

    if category:
        queryset = queryset.filter(category=category)

    if product_name:
        queryset = queryset.filter(product_name__icontains=product_name)

    # Get aggregated data
    from django.db.models import Sum, Count, Avg

    summary = queryset.aggregate(
        total_units=Sum("units_sold"),
        total_revenue=Sum("total_revenue"),
        avg_price=Avg("current_price"),
        total_days=Count("date", distinct=True),
        total_products=Count("product_name", distinct=True),
    )

    # Get daily breakdown
    daily_data = (
        queryset.values("date")
        .annotate(daily_units=Sum("units_sold"), daily_revenue=Sum("total_revenue"))
        .order_by("date")
    )

    # Get product breakdown
    product_data = (
        queryset.values("product_name", "category")
        .annotate(
            total_units=Sum("units_sold"),
            total_revenue=Sum("total_revenue"),
            avg_price=Avg("current_price"),
        )
        .order_by("-total_units")
    )

    return JsonResponse(
        {
            "success": True,
            "summary": summary,
            "daily_data": list(daily_data),
            "product_data": list(product_data),
            "record_count": queryset.count(),
        }
    )

@staff_member_required(login_url='staff_login')
def product_trend_api(request, product_id=None):
    """API endpoint to get product trend data"""
    from checkout.models import Product

    if product_id:
        try:
            product = Product.objects.get(id=product_id)
            product_name = product.name
        except Product.DoesNotExist:
            return JsonResponse({"success": False, "error": "Product not found"})
    else:
        product_name = request.GET.get("product_name")
        if not product_name:
            return JsonResponse(
                {"success": False, "error": "Product name or ID required"}
            )

    # Get sales data for this product
    sales_data = DailyProductSales.objects.filter(product_name=product_name).order_by(
        "date"
    )

    # Format for charting
    chart_data = {"labels": [], "units_sold": [], "revenue": [], "prices": []}

    for sale in sales_data:
        chart_data["labels"].append(str(sale.date))
        chart_data["units_sold"].append(float(sale.units_sold))
        chart_data["revenue"].append(float(sale.total_revenue))
        chart_data["prices"].append(float(sale.current_price))

    # Calculate statistics
    if sales_data.exists():
        total_units = sum(chart_data["units_sold"])
        total_revenue = sum(chart_data["revenue"])
        avg_daily_units = total_units / len(chart_data["units_sold"])

        return JsonResponse(
            {
                "success": True,
                "product_name": product_name,
                "chart_data": chart_data,
                "stats": {
                    "total_units": total_units,
                    "total_revenue": total_revenue,
                    "avg_daily_units": avg_daily_units,
                    "sales_days": len(chart_data["labels"]),
                    "current_price": float(sales_data.last().current_price),
                },
                "sales_data": list(
                    sales_data.values(
                        "date", "units_sold", "total_revenue", "current_price"
                    )
                ),
            }
        )
    else:
        return JsonResponse(
            {"success": False, "error": "No sales data found for this product"}
        )

@staff_member_required(login_url='staff_login')
def predict_tomorrow_price(request):
    predicted_price = None

    if request.method == "POST":
        # A. Get data from User (or Database)
        p_name = request.POST.get("product_name") # e.g. "Milk"
        p_cat = request.POST.get("category")      # e.g. "Dairy"
        inventory = float(request.POST.get("inventory"))
        expiry = request.POST.get("expiry_date")  # "2025-12-31"
       
        # B. Get Sales History (Crucial!)
        # In a real app, query your database: Sales.objects.filter(product=p_name)...
        # For this example, we ask the user:
        sales_today = float(request.POST.get("sales_today"))
        sales_yesterday = float(request.POST.get("sales_yesterday"))

        # C. Prepare Data
        input_df = prepare_input_for_tomorrow(
            p_name, p_cat, inventory, expiry, sales_today, sales_yesterday
        )

        # D. Predict
        model = ProjectsmartshelfConfig.model
        if model:
            # xgboost usually returns a numpy array like [12.50]
            result = model.predict(input_df)
            predicted_price = round(result[0], 2)

    return render(request, "predict_price.html", {"price": predicted_price})

@staff_member_required(login_url='staff_login')
def pricing_dashboard(request):
    # Get simulated date (if any) or use real today
    simulated_today = getattr(request, 'simulated_date', None)
    if simulated_today is None:
        simulated_today = timezone.now().date()
    process_promotions_for_request(request)

    # Run AI Batch Prediction using simulated date as base
    raw_results = predict_prices_batch(base_date=simulated_today)

    final_report = []
    for product, raw_ai_price in raw_results:
        current_price = product.price if product.price else Decimal('0.00')
        expiry_date = product.expiry_date
        ai_price = Decimal(str(raw_ai_price)).quantize(Decimal('0.01'))

        # Determine price trend
        if ai_price > current_price:
            status = "Increase"
            trend_icon = "⬆"
            css_class = "trend-up"
        elif ai_price < current_price:
            status = "Decrease"
            trend_icon = "⬇"
            css_class = "trend-down"
        else:
            status = "Keep"
            trend_icon = "➡"
            css_class = "trend-flat"

        # Calculate days until expiry (using simulated today)
        if product.expiry_date:
            days_until_expiry = (product.expiry_date - simulated_today).days
            if days_until_expiry < 0:
                days_until_expiry = 0  # or keep negative to show expired
        else:
            days_until_expiry = 30  # default as in prediction

        final_report.append({
            'product': product,
            'current_price': current_price,
            'current_date': simulated_today,
            'expiry_date': expiry_date,
            'ai_price': ai_price,
            'status': status,
            'icon': trend_icon,
            'css_class': css_class,
            'days_until_expiry': days_until_expiry,
        })

    # Handle "Apply All" button
    if request.method == "POST" and "apply_all" in request.POST:
        update_count = 0
        reason = get_price_change_reason(request, "Applied all AI pricing recommendations")
        for item in final_report:
            product = item['product']
            new_price = item['ai_price']
            old_price = product.price
            if new_price != old_price:
                PriceHistory.objects.create(
                    product=product,
                    changed_by=request.user,
                    old_price=old_price,
                    new_price=new_price,
                    reason=reason,
                )
                product.price = new_price
                product.save()
                # MQTT push
                send_product_lcd_update(product)
                update_count += 1
        messages.success(request, f"Successfully updated prices for {update_count} products!")
        return redirect('pricing_dashboard')

    return render(request, 'pricing_dashboard.html', {'report_data': final_report, 'current_date': simulated_today,})

@staff_member_required(login_url='staff_login')
def close_day_advance(request):
    if request.method == 'POST':
        try:
            current_date = get_active_simulated_date(request)
            if current_date:
                set_active_simulated_date(request, current_date)
            else:
                # No simulated date set yet – use the latest transaction date or today
                from checkout.models import LiveTransaction
                latest_trans = LiveTransaction.objects.order_by('-date').first()
                if latest_trans:
                    current_date = latest_trans.date
                else:
                    current_date = timezone.now().date()
                # Set the simulated date to this starting point
                set_active_simulated_date(request, current_date)

            print(f"DEBUG: Closing day for {current_date}")

            promotion_results_today = ScheduledPromotion.process_due_promotions(
                datetime.datetime.combine(current_date, datetime.time.max)
            )

            # Generate summary for that date (will include zeros for unsold products)
            records = DailyProductSales.generate_daily_summary(current_date)

            # Advance to the next day
            next_date = current_date + datetime.timedelta(days=1)
            set_active_simulated_date(request, next_date)
            promotion_results_next_day = ScheduledPromotion.process_due_promotions(
                datetime.datetime.combine(next_date, datetime.time.min)
            )
            print(f"DEBUG: Advanced to {next_date}")

            return JsonResponse({
                'success': True,
                'closed_date': str(current_date),
                'next_date': str(next_date),
                'records_created': len(records),
                'promotions': {
                    'closed_day': promotion_results_today,
                    'next_day': promotion_results_next_day,
                },
                'message': f'Closed {current_date}, now at {next_date}.'
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid method'})

@staff_member_required(login_url='staff_login')
def reset_date(request):
    if request.method == 'POST':
        clear_active_simulated_date(request)
        return JsonResponse({'success': True})
    return JsonResponse({'success': False})

@staff_member_required(login_url='staff_login')
def apply_single_price(request):
    if request.method == 'POST':
        product_id = request.POST.get('product_id')
        new_price = request.POST.get('new_price')
        try:
            product = Product.objects.get(id=product_id)
            new_price_decimal = Decimal(new_price).quantize(Decimal('0.01'))
            if product.price != new_price_decimal:
                reason = get_price_change_reason(request, "Applied AI pricing recommendation")
                # Log history
                PriceHistory.objects.create(
                    product=product,
                    changed_by=request.user,
                    old_price=product.price,
                    new_price=new_price_decimal,
                    reason=reason,
                )
                product.price = new_price_decimal
                product.save()
                # MQTT push to ESP32
                send_product_lcd_update(product)
                messages.success(request, f"Price updated for {product.name}.")
            else:
                messages.info(request, f"Price for {product.name} unchanged.")
        except Product.DoesNotExist:
            messages.error(request, "Product not found.")
        except Exception as e:
            messages.error(request, f"Error: {e}")
        return redirect('pricing_dashboard')
    return redirect('pricing_dashboard')

@staff_member_required(login_url='staff_login')
def recommendations_page(request):
    # Get simulated date (or real today)
    simulated_today = getattr(request, 'simulated_date', None)
    if simulated_today is None:
        simulated_today = timezone.now().date()
    process_promotions_for_request(request)

    recommendations = build_recommendations(simulated_today)
    request._recommendations_count = len(recommendations)

    context = {
        'recommendations': recommendations,
        'current_date': simulated_today,
    }
    return render(request, 'recommendations.html', context)


@staff_member_required(login_url='staff_login')
def price_history_view(request):
    history_list = PriceHistory.objects.select_related('product', 'changed_by').all()

    # Search query (text search on product name or username)
    search_query = request.GET.get('search', '')
    if search_query:
        history_list = history_list.filter(
            Q(product__name__icontains=search_query) |
            Q(changed_by__username__icontains=search_query) |
            Q(reason__icontains=search_query)
        )

    # Advanced filters
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    product_id = request.GET.get('product_id')
    user_id = request.GET.get('user_id')

    if start_date:
        start_date_obj = parse_date(start_date)
        if start_date_obj:
            history_list = history_list.filter(changed_at__date__gte=start_date_obj)
    if end_date:
        end_date_obj = parse_date(end_date)
        if end_date_obj:
            history_list = history_list.filter(changed_at__date__lte=end_date_obj)
    if product_id:
        history_list = history_list.filter(product_id=product_id)
    if user_id:
        history_list = history_list.filter(changed_by_id=user_id)

    # Pagination
    paginator = Paginator(history_list, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Add price difference
    for history in page_obj:
        history.price_diff = history.new_price - history.old_price

    # Get lists for filter dropdowns
    products = Product.objects.all().order_by('name')
    staff_users = User.objects.filter(is_staff=True).order_by('username')

    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'start_date': start_date,
        'end_date': end_date,
        'selected_product': product_id,
        'selected_user': user_id,
        'products': products,
        'staff_users': staff_users,
    }
    return render(request, 'price_history.html', context)


@staff_member_required(login_url='staff_login')
def export_price_history_csv(request):
    # Apply same filters as the main view (duplicate logic)
    history_list = PriceHistory.objects.select_related('product', 'changed_by').all()

    search_query = request.GET.get('search', '')
    if search_query:
        history_list = history_list.filter(
            Q(product__name__icontains=search_query) |
            Q(changed_by__username__icontains=search_query) |
            Q(reason__icontains=search_query)
        )

    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    product_id = request.GET.get('product_id')
    user_id = request.GET.get('user_id')

    if start_date:
        start_date_obj = parse_date(start_date)
        if start_date_obj:
            history_list = history_list.filter(changed_at__date__gte=start_date_obj)
    if end_date:
        end_date_obj = parse_date(end_date)
        if end_date_obj:
            history_list = history_list.filter(changed_at__date__lte=end_date_obj)
    if product_id:
        history_list = history_list.filter(product_id=product_id)
    if user_id:
        history_list = history_list.filter(changed_by_id=user_id)

    # Create CSV response
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="price_history.csv"'
    writer = csv.writer(response)
    writer.writerow(['Product', 'Changed By', 'Date & Time', 'Old Price', 'New Price', 'Price Change', 'Reason'])

    for history in history_list.order_by('-changed_at'):
        diff = history.new_price - history.old_price
        writer.writerow([
            history.product.name,
            history.changed_by.get_full_name() if history.changed_by else 'System',
            history.changed_at.strftime('%Y-%m-%d %H:%M:%S'),
            f"${history.old_price:.2f}",
            f"${history.new_price:.2f}",
            f"{'+' if diff > 0 else ''}{diff:.2f}",
            history.reason,
        ])

    return response


@staff_member_required(login_url='staff_login')
def manual_pricing_view(request):
    process_promotions_for_request(request)
    products = Product.objects.all().order_by('name')
    search_query = request.GET.get('search', '')
    category_filter = request.GET.get('category', '')

    if search_query:
        products = products.filter(name__icontains=search_query)
    if category_filter:
        products = products.filter(category=category_filter)

    # Get unique categories for filter dropdown
    categories = Product.objects.values_list('category', flat=True).distinct().order_by('category')

    # Handle single product price override (via POST from the table)
    if request.method == 'POST' and 'product_id' in request.POST:
        product_id = request.POST.get('product_id')
        try:
            product = Product.objects.get(id=product_id)
            old_price = product.price

            new_price_raw = request.POST.get('new_price', '').strip()
            discount_percent_raw = request.POST.get('discount_percent', '').strip()
            discount_fixed_raw = request.POST.get('discount_fixed', '').strip()
            reason = get_price_change_reason(request, "Manual price override")

            new_price_decimal = None

            # Priority: new_price > discount_percent > discount_fixed
            if new_price_raw:
                try:
                    new_price_decimal = Decimal(new_price_raw).quantize(Decimal('0.01'))
                except:
                    messages.error(request, f"Invalid new price format: {new_price_raw}")
                    return redirect('manual_pricing')
            elif discount_percent_raw:
                try:
                    percent = Decimal(discount_percent_raw)
                    new_price_decimal = old_price * (1 - percent / 100)
                    new_price_decimal = new_price_decimal.quantize(Decimal('0.01'))
                except:
                    messages.error(request, f"Invalid discount percentage: {discount_percent_raw}")
                    return redirect('manual_pricing')
            elif discount_fixed_raw:
                try:
                    fixed = Decimal(discount_fixed_raw)
                    new_price_decimal = old_price - fixed
                    new_price_decimal = max(new_price_decimal, Decimal('0.00')).quantize(Decimal('0.01'))
                except:
                    messages.error(request, f"Invalid fixed discount amount: {discount_fixed_raw}")
                    return redirect('manual_pricing')
            else:
                messages.error(request, "No valid price change provided.")
                return redirect('manual_pricing')

            if old_price != new_price_decimal:
                PriceHistory.objects.create(
                    product=product,
                    changed_by=request.user,
                    old_price=old_price,
                    new_price=new_price_decimal,
                    reason=reason,
                )
                product.price = new_price_decimal
                product.save()
                send_product_lcd_update(product)
                messages.success(request, f"Price for {product.name} updated from ${old_price} to ${new_price_decimal}.")
            else:
                messages.info(request, f"Price for {product.name} unchanged.")

        except Product.DoesNotExist:
            messages.error(request, "Product not found.")
        except Exception as e:
            messages.error(request, f"Unexpected error: {e}")
        return redirect('manual_pricing')

    # Handle bulk discount by category
    if request.method == 'POST' and 'bulk_category' in request.POST:
        category = request.POST.get('bulk_category')
        discount_type = request.POST.get('discount_type')  # 'percentage' or 'fixed'
        discount_value = request.POST.get('discount_value')
        reason = get_price_change_reason(request, f"Bulk {discount_type} discount for {category}")
        try:
            discount_value = Decimal(discount_value)
            products_in_cat = Product.objects.filter(category=category)
            updated_count = 0
            for prod in products_in_cat:
                old_price = prod.price
                if discount_type == 'percentage':
                    new_price = old_price * (1 - discount_value / 100)
                else:  # fixed amount
                    new_price = old_price - discount_value
                # Ensure price doesn't go below zero
                new_price = max(new_price, Decimal('0.00'))
                new_price = new_price.quantize(Decimal('0.01'))
                if old_price != new_price:
                    PriceHistory.objects.create(
                        product=prod,
                        changed_by=request.user,
                        old_price=old_price,
                        new_price=new_price,
                        reason=reason,
                    )
                    prod.price = new_price
                    prod.save()
                    send_product_lcd_update(prod)
                    updated_count += 1
            messages.success(request, f"Bulk discount applied to {updated_count} products in '{category}' category.")
        except Exception as e:
            messages.error(request, f"Error applying bulk discount: {e}")
        return redirect('manual_pricing')

    context = {
        'products': products,
        'search_query': search_query,
        'category_filter': category_filter,
        'categories': categories,
    }
    return render(request, 'manual_pricing.html', context)


@staff_member_required(login_url='staff_login')
def promotions_view(request):
    effective_at = get_effective_promotion_time(request)
    effective_date = effective_at.date()
    ScheduledPromotion.process_due_promotions(effective_at)

    if request.method == 'POST':
        action = request.POST.get('action', 'create')

        if action == 'process_due':
            results = ScheduledPromotion.process_due_promotions(effective_at)
            messages.success(
                request,
                (
                    f"Processed promotions for {effective_at:%Y-%m-%d %H:%M:%S}: "
                    f"{results['activated']} activated, {results['reverted']} reverted."
                ),
            )
            return redirect('promotions')

        promotion_id = request.POST.get('promotion_id')
        if action == 'cancel' and promotion_id:
            try:
                promotion = ScheduledPromotion.objects.select_related('product').get(id=promotion_id)
                if promotion.cancel():
                    if promotion.status == ScheduledPromotion.STATUS_CANCELLED:
                        messages.success(request, f"Cancelled {promotion.display_name}.")
                    else:
                        messages.info(request, f"No changes made for {promotion.display_name}.")
                else:
                    messages.info(request, f"{promotion.display_name} is already closed.")
            except ScheduledPromotion.DoesNotExist:
                messages.error(request, "Promotion not found.")
            return redirect('promotions')

        if action == 'create':
            try:
                product = Product.objects.get(id=request.POST.get('product_id'))
            except Product.DoesNotExist:
                messages.error(request, "Choose a valid product.")
                return redirect('promotions')

            title = request.POST.get('title', '').strip()
            start_date = parse_date(request.POST.get('start_date', '').strip())
            start_time_raw = request.POST.get('start_time', '').strip()
            start_time = parse_time(start_time_raw) if start_time_raw else None
            end_date = parse_date(request.POST.get('end_date', '').strip())
            end_time_raw = request.POST.get('end_time', '').strip()
            end_time = parse_time(end_time_raw) if end_time_raw else None
            start_delay_seconds_raw = request.POST.get('start_delay_seconds', '').strip()
            duration_minutes_raw = request.POST.get('duration_minutes', '').strip()
            duration_seconds_raw = request.POST.get('duration_seconds', '').strip()
            promo_price_raw = request.POST.get('promotional_price', '').strip()
            discount_percent_raw = request.POST.get('discount_percent', '').strip()
            discount_fixed_raw = request.POST.get('discount_fixed', '').strip()

            if start_delay_seconds_raw:
                try:
                    start_delay_seconds = int(start_delay_seconds_raw)
                    if start_delay_seconds < 0:
                        raise ValueError
                except ValueError:
                    messages.error(request, "Start delay must be zero or a positive number of seconds.")
                    return redirect('promotions')

                starts_at = effective_at + datetime.timedelta(seconds=start_delay_seconds)
                start_date = starts_at.date()
                start_time = starts_at.time().replace(microsecond=0)

            if not start_date:
                messages.error(request, "Start date is required.")
                return redirect('promotions')

            duration_supplied = bool(duration_minutes_raw or duration_seconds_raw)
            if duration_supplied:
                try:
                    duration_minutes = int(duration_minutes_raw) if duration_minutes_raw else 0
                    duration_seconds = int(duration_seconds_raw) if duration_seconds_raw else 0
                    duration_total_seconds = (duration_minutes * 60) + duration_seconds
                    if duration_minutes < 0 or duration_seconds < 0 or duration_total_seconds <= 0:
                        raise ValueError
                except ValueError:
                    messages.error(request, "Duration must be a positive number of seconds or minutes.")
                    return redirect('promotions')

                if not start_time:
                    messages.error(request, "Start time is required when using duration.")
                    return redirect('promotions')

                starts_at = datetime.datetime.combine(start_date, start_time)
                ends_at = starts_at + datetime.timedelta(seconds=duration_total_seconds)
                end_date = ends_at.date()
                end_time = ends_at.time().replace(microsecond=0)
            elif start_delay_seconds_raw and start_time and not end_time:
                if not end_date or end_date < start_date:
                    end_date = start_date
                end_time = datetime.time.max
            elif start_time or end_time:
                if not start_time or not end_time:
                    messages.error(request, "Timed promotions need both start and end times.")
                    return redirect('promotions')
                if not end_date:
                    end_date = start_date
            elif not end_date:
                end_date = start_date

            if end_date < start_date:
                messages.error(request, "End must be on or after the start.")
                return redirect('promotions')
            if ScheduledPromotion._combine_schedule(end_date, end_time, is_end=True) < ScheduledPromotion._combine_schedule(start_date, start_time, is_end=False):
                messages.error(request, "End must be after the start time.")
                return redirect('promotions')

            try:
                if promo_price_raw:
                    promotional_price = Decimal(promo_price_raw)
                elif discount_percent_raw:
                    percent = Decimal(discount_percent_raw)
                    promotional_price = product.price * (1 - percent / Decimal('100'))
                elif discount_fixed_raw:
                    fixed_amount = Decimal(discount_fixed_raw)
                    promotional_price = product.price - fixed_amount
                else:
                    messages.error(request, "Enter a promotional price or discount.")
                    return redirect('promotions')
                promotional_price = max(promotional_price, Decimal('0.00')).quantize(Decimal('0.01'))
            except Exception:
                messages.error(request, "Enter a valid promotional price or discount.")
                return redirect('promotions')

            if promotional_price >= product.price:
                messages.error(request, "Promotional price must be lower than the current product price.")
                return redirect('promotions')

            overlapping = ScheduledPromotion.overlapping_for(
                product,
                start_date,
                end_date,
                start_time=start_time,
                end_time=end_time,
            )
            if overlapping:
                messages.error(request, f"{product.name} already has an active or scheduled promotion in that date range.")
                return redirect('promotions')

            promotion = ScheduledPromotion(
                product=product,
                title=title,
                promotional_price=promotional_price,
                start_date=start_date,
                start_time=start_time,
                end_date=end_date,
                end_time=end_time,
                created_by=request.user,
            )
            promotion.full_clean()
            promotion.save()
            ScheduledPromotion.process_due_promotions(effective_at)
            messages.success(request, f"Scheduled {promotion.display_name} for {product.name}.")
            return redirect('promotions')

    promotions = ScheduledPromotion.objects.select_related('product', 'created_by').order_by('-created_at')
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '').strip()

    if search_query:
        promotions = promotions.filter(
            Q(product__name__icontains=search_query) |
            Q(title__icontains=search_query)
        )
    if status_filter:
        promotions = promotions.filter(status=status_filter)
    promotions = prepare_promotions_for_display(promotions, effective_at)

    products = Product.objects.all().order_by('name')
    status_counts = {
        'scheduled': ScheduledPromotion.objects.filter(status=ScheduledPromotion.STATUS_SCHEDULED).count(),
        'active': ScheduledPromotion.objects.filter(status=ScheduledPromotion.STATUS_ACTIVE).count(),
        'expired': ScheduledPromotion.objects.filter(status=ScheduledPromotion.STATUS_EXPIRED).count(),
        'cancelled': ScheduledPromotion.objects.filter(status=ScheduledPromotion.STATUS_CANCELLED).count(),
    }

    context = {
        'products': products,
        'promotions': promotions,
        'search_query': search_query,
        'status_filter': status_filter,
        'status_choices': ScheduledPromotion.STATUS_CHOICES,
        'status_counts': status_counts,
        'effective_date': effective_date,
        'effective_at': effective_at,
        'active_simulated_date': get_active_simulated_date(request),
        'default_start_date': effective_date.strftime('%Y-%m-%d'),
        'default_start_time': effective_at.strftime('%H:%M:%S'),
    }
    return render(request, 'promotions.html', context)


@staff_member_required(login_url='staff_login')
def inventory_management_view(request):
    products = Product.objects.all().order_by('name')
    search_query = request.GET.get('search', '')
    category_filter = request.GET.get('category', '')
    low_stock_filter = request.GET.get('low_stock', '')

    if search_query:
        products = products.filter(name__icontains=search_query)
    if category_filter:
        products = products.filter(category=category_filter)
    if low_stock_filter:
        try:
            threshold = int(low_stock_filter)
            products = products.filter(inventory_level__lte=threshold)
        except ValueError:
            pass  # Ignore invalid threshold

    # Get unique categories for filter dropdown
    categories = Product.objects.values_list('category', flat=True).distinct().order_by('category')

    # Handle inventory update
    if request.method == 'POST' and 'product_id' in request.POST:
        product_id = request.POST.get('product_id')
        try:
            product = Product.objects.get(id=product_id)
            old_inventory = product.inventory_level

            new_inventory_raw = request.POST.get('new_inventory', '').strip()
            adjustment_raw = request.POST.get('adjustment', '').strip()
            expiry_date_raw = request.POST.get('expiry_date', '').strip()

            new_inventory = old_inventory  # default

            if new_inventory_raw:
                try:
                    new_inventory = int(new_inventory_raw)
                    if new_inventory < 0:
                        messages.error(request, "Inventory level cannot be negative.")
                        return redirect('inventory_management')
                except ValueError:
                    messages.error(request, f"Invalid inventory value: {new_inventory_raw}")
                    return redirect('inventory_management')
            elif adjustment_raw:
                try:
                    adjustment = int(adjustment_raw)
                    new_inventory = old_inventory + adjustment
                    if new_inventory < 0:
                        messages.error(request, "Inventory level cannot be negative.")
                        return redirect('inventory_management')
                except ValueError:
                    messages.error(request, f"Invalid adjustment value: {adjustment_raw}")
                    return redirect('inventory_management')
            else:
                messages.error(request, "No inventory change provided.")
                return redirect('inventory_management')

            # If adding new stock (inventory increasing), require expiry date
            if new_inventory > old_inventory:
                if not expiry_date_raw:
                    messages.error(request, "Expiry date is required when adding new stock.")
                    return redirect('inventory_management')
                try:
                    from django.utils.dateparse import parse_date
                    expiry_date = parse_date(expiry_date_raw)
                    if not expiry_date:
                        raise ValueError
                    product.expiry_date = expiry_date
                except ValueError:
                    messages.error(request, f"Invalid expiry date format: {expiry_date_raw}")
                    return redirect('inventory_management')

            product.inventory_level = new_inventory
            product.save()

            if new_inventory_raw:
                messages.success(request, f"Inventory for {product.name} updated from {old_inventory} to {new_inventory}.")
            else:
                messages.success(request, f"Inventory for {product.name} adjusted by {int(adjustment_raw)} (from {old_inventory} to {new_inventory}).")

        except Product.DoesNotExist:
            messages.error(request, "Product not found.")
        except Exception as e:
            messages.error(request, f"Unexpected error: {e}")
        return redirect('inventory_management')

    # Calculate some stats
    total_products = Product.objects.count()
    low_stock_products = Product.objects.filter(inventory_level__lte=10).count()
    out_of_stock_products = Product.objects.filter(inventory_level=0).count()

    context = {
        'products': products,
        'search_query': search_query,
        'category_filter': category_filter,
        'low_stock_filter': low_stock_filter,
        'categories': categories,
        'total_products': total_products,
        'low_stock_products': low_stock_products,
        'out_of_stock_products': out_of_stock_products,
    }
    return render(request, 'inventory_management.html', context)


@staff_member_required(login_url='staff_login')
def lcd_management_view(request):
    process_promotions_for_request(request)
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'sync_all':
            success_count = 0
            failed_count = 0
            skipped_count = 0
            active_promotions = get_active_promotions_by_product_id()

            for product in Product.objects.all().order_by('lcd_id', 'name'):
                if product.lcd_id not in AVAILABLE_LCD_IDS:
                    skipped_count += 1
                    continue
                if send_product_lcd_update(product, active_promotions.get(product.id)):
                    success_count += 1
                else:
                    failed_count += 1

            if failed_count:
                messages.warning(
                    request,
                    f"Synced {success_count} product displays. {failed_count} update(s) failed. {skipped_count} unassigned.",
                )
            else:
                messages.success(request, f"Synced {success_count} product displays. {skipped_count} unassigned.")
            return redirect('lcd_management')

        product_id = request.POST.get('product_id')

        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            messages.error(request, "Product not found.")
            return redirect('lcd_management')

        if action == 'sync_product':
            if product.lcd_id not in AVAILABLE_LCD_IDS:
                messages.warning(request, f"{product.name} is not assigned to an LCD.")
                return redirect('lcd_management')
            if send_product_lcd_update(product):
                messages.success(request, f"Synced {product.name} to LCD {product.lcd_id}.")
            else:
                messages.warning(request, f"Could not sync {product.name} to LCD {product.lcd_id}.")
            return redirect('lcd_management')

        if action == 'update_assignment':
            lcd_id_raw = request.POST.get('lcd_id', '').strip()

            try:
                lcd_id = int(lcd_id_raw)
                if lcd_id != 0 and lcd_id not in AVAILABLE_LCD_IDS:
                    raise ValueError
            except ValueError:
                messages.error(request, "Choose an available LCD or Not assigned.")
                return redirect('lcd_management')

            if lcd_id != 0:
                conflicting_product = (
                    Product.objects.filter(lcd_id=lcd_id)
                    .exclude(id=product.id)
                    .first()
                )
                if conflicting_product:
                    messages.error(
                        request,
                        f"LCD {lcd_id} is already assigned to {conflicting_product.name}.",
                    )
                    return redirect('lcd_management')

            product.lcd_id = lcd_id
            product.save(update_fields=['lcd_id'])

            if lcd_id == 0:
                messages.success(request, f"{product.name} is now marked Not assigned.")
            else:
                messages.success(request, f"{product.name} assigned to LCD {lcd_id}.")
            return redirect('lcd_management')

        messages.error(request, "Unknown LCD management action.")
        return redirect('lcd_management')

    products = Product.objects.all()
    search_query = request.GET.get('search', '')
    category_filter = request.GET.get('category', '')
    lcd_filter = request.GET.get('lcd_id', '')

    if search_query:
        products = products.filter(name__icontains=search_query)
    if category_filter:
        products = products.filter(category=category_filter)
    if lcd_filter:
        if lcd_filter == 'unassigned':
            products = products.exclude(lcd_id__in=AVAILABLE_LCD_IDS)
        else:
            try:
                lcd_id = int(lcd_filter)
                if lcd_id in AVAILABLE_LCD_IDS:
                    products = products.filter(lcd_id=lcd_id)
            except ValueError:
                pass

    categories = Product.objects.values_list('category', flat=True).distinct().order_by('category')
    occupied_lcd_ids = set(
        Product.objects.filter(lcd_id__in=AVAILABLE_LCD_IDS)
        .values_list('lcd_id', flat=True)
    )
    products = list(products)
    products.sort(
        key=lambda product: (
            0 if product.lcd_id in AVAILABLE_LCD_IDS else 1,
            product.lcd_id if product.lcd_id in AVAILABLE_LCD_IDS else len(AVAILABLE_LCD_IDS) + 1,
            product.name.lower(),
        )
    )
    for product in products:
        product.is_lcd_assigned = product.lcd_id in AVAILABLE_LCD_IDS
        product.active_promotion = None
        other_occupied_lcd_ids = occupied_lcd_ids.copy()
        if product.is_lcd_assigned:
            other_occupied_lcd_ids.discard(product.lcd_id)
        product.assignment_lcd_ids = [
            lcd_id for lcd_id in AVAILABLE_LCD_IDS
            if lcd_id not in other_occupied_lcd_ids
        ]

    active_promotions = get_active_promotions_by_product_id([product.id for product in products])
    for product in products:
        product.active_promotion = active_promotions.get(product.id)

    assigned_products = Product.objects.filter(lcd_id__in=AVAILABLE_LCD_IDS).count()
    unassigned_products = Product.objects.exclude(lcd_id__in=AVAILABLE_LCD_IDS).count()

    context = {
        'products': products,
        'search_query': search_query,
        'category_filter': category_filter,
        'lcd_filter': lcd_filter,
        'categories': categories,
        'available_lcd_ids': AVAILABLE_LCD_IDS,
        'total_products': Product.objects.count(),
        'available_display_count': len(AVAILABLE_LCD_IDS),
        'assigned_products': assigned_products,
        'unassigned_products': unassigned_products,
        'active_promotion_count': len(active_promotions),
    }
    return render(request, 'lcd_management.html', context)

