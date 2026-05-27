import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from checkout.models import Product
from ProjectSmartShelf.ml_models import utils as pricing_utils
from ProjectSmartShelf.models import DailyProductSales, PriceHistory, ScheduledPromotion
from ProjectSmartShelf.utils import send_price_update
from ProjectSmartShelf.views import prepare_promotions_for_display


class LCDPayloadFormatTests(TestCase):
    @patch('ProjectSmartShelf.utils.publish.single')
    def test_promotion_payload_uses_product_promo_and_plain_price_line(self, mock_publish):
        result = send_price_update(2, 'Milk', '9.99', promotion_label='PROMO')

        self.assertTrue(result)
        mock_publish.assert_called_once()
        _, payload = mock_publish.call_args.args[:2]
        self.assertEqual(payload, 'Milk (PROMO)|9.99')

    @patch('ProjectSmartShelf.utils.publish.single')
    def test_standard_payload_uses_plain_currency_price_line(self, mock_publish):
        result = send_price_update(2, 'Milk', Decimal('12.50'))

        self.assertTrue(result)
        _, payload = mock_publish.call_args.args[:2]
        self.assertEqual(payload, 'Milk|12.50')


class SidebarRecommendationBadgeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='staff',
            password='password123',
            is_staff=True,
        )
        self.client.login(username='staff', password='password123')

    @patch('ProjectSmartShelf.recommendations.recommendation_count', return_value=3)
    def test_sidebar_shows_recommendation_count_badge(self, mock_recommendation_count):
        response = self.client.get(reverse('staff_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'recommendation-badge')
        self.assertContains(response, '>3<')

    @patch('ProjectSmartShelf.recommendations.recommendation_count', return_value=0)
    def test_sidebar_hides_badge_when_no_recommendations(self, mock_recommendation_count):
        response = self.client.get(reverse('staff_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'recommendation-badge')


class ReportsPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='staff',
            password='password123',
            is_staff=True,
        )
        self.client.login(username='staff', password='password123')
        self.report_date = datetime.date(2026, 5, 18)
        DailyProductSales.objects.create(
            date=self.report_date,
            day_of_week='Monday',
            category='Dairy',
            product_name='Milk',
            current_price=Decimal('12.50'),
            units_sold=4,
        )
        DailyProductSales.objects.create(
            date=self.report_date,
            day_of_week='Monday',
            category='Bakery',
            product_name='Bread',
            current_price=Decimal('8.75'),
            units_sold=2,
        )

    def test_reports_page_renders_sales_summary(self):
        response = self.client.get(reverse('reports'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sales Reports')
        self.assertContains(response, 'Milk')
        self.assertContains(response, '$67.50')
        self.assertContains(response, 'Export CSV')
        self.assertContains(response, 'href="/ProjectSmartShelf/reports/"')

    def test_reports_page_filters_by_category(self):
        response = self.client.get(reverse('reports'), {'category': 'Dairy'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['product_data']), 1)
        self.assertEqual(response.context['product_data'][0]['product_name'], 'Milk')
        self.assertEqual(response.context['summary']['total_revenue'], Decimal('50.00'))

    def test_reports_export_csv_uses_current_filters(self):
        response = self.client.get(reverse('export_sales_report_csv'), {'category': 'Dairy'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        content = response.content.decode()
        self.assertIn('Milk', content)
        self.assertIn('$50.00', content)
        self.assertNotIn('Bread', content)


class PricingDemandLogicTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='staff',
            password='password123',
            is_staff=True,
        )
        self.client.login(username='staff', password='password123')
        self.base_date = datetime.date(2026, 5, 21)
        self.product = Product.objects.create(
            name='Milk',
            category='Dairy',
            price=Decimal('12.50'),
            inventory_level=20,
            expiry_date=self.base_date + datetime.timedelta(days=5),
        )

    def create_daily_sale(self, sale_date, units_sold):
        return DailyProductSales.objects.create(
            date=sale_date,
            day_of_week=sale_date.strftime('%A'),
            category=self.product.category,
            product_name=self.product.name,
            current_price=self.product.price,
            units_sold=units_sold,
        )

    @patch('ProjectSmartShelf.views.predict_prices_batch')
    def test_pricing_dashboard_demand_uses_yesterday_and_day_before_yesterday(self, mock_predict_prices_batch):
        mock_predict_prices_batch.return_value = [(self.product, Decimal('13.25'))]
        self.create_daily_sale(self.base_date, 99)
        self.create_daily_sale(self.base_date - datetime.timedelta(days=1), 8)
        self.create_daily_sale(self.base_date - datetime.timedelta(days=2), 4)
        session = self.client.session
        session['simulated_date'] = self.base_date.strftime('%Y-%m-%d')
        session.save()

        response = self.client.get(reverse('pricing_dashboard'))

        self.assertEqual(response.status_code, 200)
        item = response.context['report_data'][0]
        self.assertEqual(item['sales_lag_1'], 8)
        self.assertEqual(item['sales_lag_2'], 4)
        self.assertEqual(item['demand_change'], 100)
        self.assertIn('8 sold yesterday vs 4 the day before', item['pricing_reason'])

    @patch('ProjectSmartShelf.views.predict_prices_batch')
    def test_pricing_dashboard_suppresses_small_same_day_ai_followup(self, mock_predict_prices_batch):
        self.product.price = Decimal('10.00')
        self.product.expiry_date = self.base_date + datetime.timedelta(days=10)
        self.product.save()
        PriceHistory.objects.create(
            product=self.product,
            changed_by=self.user,
            old_price=Decimal('12.50'),
            new_price=Decimal('10.00'),
            reason='Applied AI pricing recommendation',
        )
        mock_predict_prices_batch.return_value = [(self.product, Decimal('10.20'))]
        session = self.client.session
        session['simulated_date'] = self.base_date.strftime('%Y-%m-%d')
        session.save()

        response = self.client.get(reverse('pricing_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['report_data'], [])

    @patch('ProjectSmartShelf.views.predict_prices_batch')
    def test_pricing_dashboard_allows_meaningful_same_day_ai_followup(self, mock_predict_prices_batch):
        self.product.price = Decimal('10.00')
        self.product.expiry_date = self.base_date + datetime.timedelta(days=10)
        self.product.save()
        PriceHistory.objects.create(
            product=self.product,
            changed_by=self.user,
            old_price=Decimal('12.50'),
            new_price=Decimal('10.00'),
            reason='Applied AI pricing recommendation',
        )
        mock_predict_prices_batch.return_value = [(self.product, Decimal('10.80'))]
        session = self.client.session
        session['simulated_date'] = self.base_date.strftime('%Y-%m-%d')
        session.save()

        response = self.client.get(reverse('pricing_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['report_data']), 1)
        self.assertEqual(response.context['report_data'][0]['ai_price'], Decimal('10.80'))

    def test_batch_prediction_features_use_completed_sales_days(self):
        class FakeEncoder:
            def transform(self, values):
                return [0 for _ in values]

        class FakeModel:
            feature_names_in_ = [
                'product_name',
                'category',
                'current_price',
                'inventory_level',
                'days_until_expiry',
                'sales_lag_1',
                'sales_lag_2',
                'demand_change',
                'year',
                'month',
                'day',
                'day_of_week',
                'is_weekend',
            ]

            def predict(self, df):
                self.last_df = df.copy()
                return [13.25]

        fake_model = FakeModel()
        self.create_daily_sale(self.base_date, 99)
        self.create_daily_sale(self.base_date - datetime.timedelta(days=1), 8)
        self.create_daily_sale(self.base_date - datetime.timedelta(days=2), 4)

        with patch.object(pricing_utils.ProjectsmartshelfConfig, 'model', fake_model), \
             patch.object(pricing_utils.ProjectsmartshelfConfig, 'prod_encoder', FakeEncoder()), \
             patch.object(pricing_utils.ProjectsmartshelfConfig, 'cat_encoder', FakeEncoder()):
            results = pricing_utils.predict_prices_batch(base_date=self.base_date)

        self.assertEqual(results, [(self.product, 13.25)])
        self.assertEqual(fake_model.last_df.loc[0, 'sales_lag_1'], 8)
        self.assertEqual(fake_model.last_df.loc[0, 'sales_lag_2'], 4)
        self.assertEqual(fake_model.last_df.loc[0, 'demand_change'], 100)
        self.assertEqual(fake_model.last_df.loc[0, 'current_price'], 12.5)

    def test_advanced_prediction_uses_submitted_current_price(self):
        class FakeEncoder:
            def transform(self, values):
                return [0 for _ in values]

        class FakeModel:
            feature_names_in_ = [
                'product_name',
                'category',
                'current_price',
                'inventory_level',
                'days_until_expiry',
                'sales_lag_1',
                'sales_lag_2',
                'demand_change',
                'year',
                'month',
                'day',
                'day_of_week',
                'is_weekend',
            ]

            def predict(self, df):
                if not hasattr(self, 'captured_dfs'):
                    self.captured_dfs = []
                self.captured_dfs.append(df.copy())
                self.last_df = df.copy()
                return [9.99]

        fake_model = FakeModel()

        with patch.object(pricing_utils.ProjectsmartshelfConfig, 'model', fake_model), \
             patch.object(pricing_utils.ProjectsmartshelfConfig, 'prod_encoder', FakeEncoder()), \
             patch.object(pricing_utils.ProjectsmartshelfConfig, 'cat_encoder', FakeEncoder()):
            response = self.client.post(reverse('predict_price'), {
                'prediction_mode': 'advanced',
                'advanced_product_id': str(self.product.id),
                'advanced_current_price': '9.75',
                'advanced_inventory': '20',
                'advanced_days_until_expiry': '2',
                'prediction_date': self.base_date.strftime('%Y-%m-%d'),
                'sales_lag_1': '8',
                'sales_lag_2': '4',
            })

        self.assertEqual(response.status_code, 200)
        captured_prices = [df.loc[0, 'current_price'] for df in fake_model.captured_dfs]
        captured_expiry_days = [df.loc[0, 'days_until_expiry'] for df in fake_model.captured_dfs]
        self.assertIn(9.75, captured_prices)
        self.assertIn(2, captured_expiry_days)


class ScheduledPromotionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='staff',
            password='password123',
            is_staff=True,
        )
        self.client.login(username='staff', password='password123')
        self.product = Product.objects.create(
            name='Milk',
            category='Dairy',
            price=Decimal('12.50'),
            inventory_level=20,
            lcd_id=2,
        )
        self.today = timezone.localdate()

    @patch('ProjectSmartShelf.utils.send_price_update', return_value=True)
    def test_due_promotion_activates_price_and_lcd_marker(self, mock_send_price_update):
        promotion = ScheduledPromotion.objects.create(
            product=self.product,
            title='Morning sale',
            promotional_price=Decimal('9.99'),
            start_date=self.today,
            end_date=self.today + datetime.timedelta(days=1),
            created_by=self.user,
        )

        results = ScheduledPromotion.process_due_promotions(self.today)

        self.assertEqual(results['activated'], 1)
        self.product.refresh_from_db()
        promotion.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('9.99'))
        self.assertEqual(promotion.status, ScheduledPromotion.STATUS_ACTIVE)
        self.assertEqual(promotion.pre_promotion_price, Decimal('12.50'))
        self.assertEqual(PriceHistory.objects.count(), 1)
        self.assertEqual(
            PriceHistory.objects.first().reason,
            'Promotion started: Morning sale',
        )
        mock_send_price_update.assert_called_once_with(
            2,
            'Milk',
            '9.99',
            promotion_label='PROMO',
        )

    @patch('ProjectSmartShelf.utils.send_price_update', return_value=True)
    def test_expired_promotion_reverts_to_pre_promotion_price(self, mock_send_price_update):
        promotion = ScheduledPromotion.objects.create(
            product=self.product,
            title='One day sale',
            promotional_price=Decimal('9.99'),
            start_date=self.today,
            end_date=self.today,
        )
        ScheduledPromotion.process_due_promotions(self.today)
        mock_send_price_update.reset_mock()

        results = ScheduledPromotion.process_due_promotions(self.today + datetime.timedelta(days=1))

        self.assertEqual(results['reverted'], 1)
        self.product.refresh_from_db()
        promotion.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('12.50'))
        self.assertEqual(promotion.status, ScheduledPromotion.STATUS_EXPIRED)
        self.assertEqual(PriceHistory.objects.count(), 2)
        self.assertEqual(
            PriceHistory.objects.first().reason,
            'Promotion ended: One day sale',
        )
        mock_send_price_update.assert_called_once_with(2, 'Milk', '12.50')

    @patch('ProjectSmartShelf.utils.send_price_update', return_value=True)
    def test_timed_promotion_reverts_after_end_time(self, mock_send_price_update):
        promotion = ScheduledPromotion.objects.create(
            product=self.product,
            title='One hour sale',
            promotional_price=Decimal('9.99'),
            start_date=self.today,
            start_time=datetime.time(9, 0),
            end_date=self.today,
            end_time=datetime.time(10, 0),
        )

        ScheduledPromotion.process_due_promotions(
            datetime.datetime.combine(self.today, datetime.time(9, 30))
        )
        self.product.refresh_from_db()
        promotion.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('9.99'))
        self.assertEqual(promotion.status, ScheduledPromotion.STATUS_ACTIVE)

        mock_send_price_update.reset_mock()
        results = ScheduledPromotion.process_due_promotions(
            datetime.datetime.combine(self.today, datetime.time(10, 1))
        )

        self.assertEqual(results['reverted'], 1)
        self.product.refresh_from_db()
        promotion.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('12.50'))
        self.assertEqual(promotion.status, ScheduledPromotion.STATUS_EXPIRED)
        mock_send_price_update.assert_called_once_with(2, 'Milk', '12.50')

    @patch('ProjectSmartShelf.utils.send_price_update', return_value=True)
    def test_promotions_view_schedules_and_activates_current_day_promotion(self, mock_send_price_update):
        response = self.client.post(
            reverse('promotions'),
            {
                'action': 'create',
                'product_id': self.product.id,
                'title': 'Today sale',
                'start_date': self.today.strftime('%Y-%m-%d'),
                'end_date': self.today.strftime('%Y-%m-%d'),
                'promotional_price': '8.50',
            },
        )

        self.assertRedirects(response, reverse('promotions'))
        self.product.refresh_from_db()
        promotion = ScheduledPromotion.objects.get(product=self.product)
        self.assertEqual(self.product.price, Decimal('8.50'))
        self.assertEqual(promotion.status, ScheduledPromotion.STATUS_ACTIVE)
        mock_send_price_update.assert_called_once_with(
            2,
            'Milk',
            '8.50',
            promotion_label='PROMO',
        )

    @patch('ProjectSmartShelf.utils.send_price_update', return_value=True)
    def test_promotions_view_accepts_duration_minutes(self, mock_send_price_update):
        current_time = timezone.localtime()
        start_time = current_time.strftime('%H:%M')

        response = self.client.post(
            reverse('promotions'),
            {
                'action': 'create',
                'product_id': self.product.id,
                'title': 'Flash sale',
                'start_date': current_time.date().strftime('%Y-%m-%d'),
                'start_time': start_time,
                'duration_minutes': '60',
                'promotional_price': '8.75',
            },
        )

        self.assertRedirects(response, reverse('promotions'))
        promotion = ScheduledPromotion.objects.get(product=self.product)
        expected_start = datetime.datetime.combine(
            current_time.date(),
            datetime.datetime.strptime(start_time, '%H:%M').time(),
        )
        expected_end = expected_start + datetime.timedelta(minutes=60)
        self.assertEqual(promotion.end_date, expected_end.date())
        self.assertEqual(promotion.end_time, expected_end.time().replace(second=0, microsecond=0))
        self.assertEqual(promotion.status, ScheduledPromotion.STATUS_ACTIVE)

    @patch('ProjectSmartShelf.utils.send_price_update', return_value=True)
    def test_promotions_view_accepts_start_delay_and_duration_seconds(self, mock_send_price_update):
        effective_at = ScheduledPromotion._combine_schedule(
            self.today,
            datetime.time(8, 0, 10),
        )

        with patch('ProjectSmartShelf.views.get_effective_promotion_time', return_value=effective_at):
            response = self.client.post(
                reverse('promotions'),
                {
                    'action': 'create',
                    'product_id': self.product.id,
                    'title': 'Five second flash sale',
                    'start_delay_seconds': '5',
                    'duration_seconds': '10',
                    'promotional_price': '8.25',
                },
            )

        self.assertRedirects(response, reverse('promotions'))
        self.product.refresh_from_db()
        promotion = ScheduledPromotion.objects.get(product=self.product)
        self.assertEqual(self.product.price, Decimal('12.50'))
        self.assertEqual(promotion.status, ScheduledPromotion.STATUS_SCHEDULED)
        self.assertEqual(promotion.start_date, self.today)
        self.assertEqual(promotion.start_time, datetime.time(8, 0, 15))
        self.assertEqual(promotion.end_date, self.today)
        self.assertEqual(promotion.end_time, datetime.time(8, 0, 25))
        mock_send_price_update.assert_not_called()

        ScheduledPromotion.process_due_promotions(effective_at + datetime.timedelta(seconds=5))
        self.product.refresh_from_db()
        promotion.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('8.25'))
        self.assertEqual(promotion.status, ScheduledPromotion.STATUS_ACTIVE)

        ScheduledPromotion.process_due_promotions(effective_at + datetime.timedelta(seconds=16))
        self.product.refresh_from_db()
        promotion.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('12.50'))
        self.assertEqual(promotion.status, ScheduledPromotion.STATUS_EXPIRED)

    def test_promotion_countdown_uses_supplied_simulated_time(self):
        promotion = ScheduledPromotion.objects.create(
            product=self.product,
            title='Simulated sale',
            promotional_price=Decimal('9.99'),
            start_date=self.today,
            start_time=datetime.time(12, 0),
            end_date=self.today,
            end_time=datetime.time(13, 0),
        )
        simulated_now = ScheduledPromotion._combine_schedule(
            self.today,
            datetime.time(11, 30),
        )

        prepared = prepare_promotions_for_display([promotion], simulated_now)

        self.assertEqual(prepared[0].countdown_label, 'Starts in')
        self.assertEqual(prepared[0].countdown_seconds, 1800)

    @patch('ProjectSmartShelf.views.send_product_lcd_update', return_value=True)
    def test_manual_price_update_records_reason(self, mock_send_product_lcd_update):
        response = self.client.post(
            reverse('manual_pricing'),
            {
                'product_id': self.product.id,
                'new_price': '11.25',
                'price_change_reason': 'Manager override',
            },
        )

        self.assertRedirects(response, reverse('manual_pricing'))
        history = PriceHistory.objects.get(product=self.product)
        self.assertEqual(history.reason, 'Manager override')


class LCDManagementViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='staff',
            password='password123',
            is_staff=True,
        )
        self.client.login(username='staff', password='password123')
        self.product = Product.objects.create(
            name='Milk',
            category='Dairy',
            price=Decimal('12.50'),
            inventory_level=20,
            lcd_id=2,
        )

    def test_lcd_management_page_renders_products_and_available_lcds(self):
        response = self.client.get(reverse('lcd_management'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'LCD Management')
        self.assertContains(response, 'Milk')
        self.assertContains(response, 'LCD 1')
        self.assertContains(response, 'LCD 2')
        self.assertContains(response, 'LCD 3')
        self.assertContains(response, 'Refresh')
        self.assertNotContains(response, 'Display Routing')

    def test_update_assignment_sets_lcd_id(self):
        response = self.client.post(
            reverse('lcd_management'),
            {
                'action': 'update_assignment',
                'product_id': self.product.id,
                'lcd_id': '3',
            },
        )

        self.assertRedirects(response, reverse('lcd_management'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.lcd_id, 3)

    def test_update_assignment_rejects_lcd_already_assigned_to_another_product(self):
        Product.objects.create(
            name='Bread',
            category='Bakery',
            price=Decimal('8.75'),
            inventory_level=12,
            lcd_id=1,
        )

        response = self.client.post(
            reverse('lcd_management'),
            {
                'action': 'update_assignment',
                'product_id': self.product.id,
                'lcd_id': '1',
            },
        )

        self.assertRedirects(response, reverse('lcd_management'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.lcd_id, 2)

    def test_assignment_options_exclude_lcd_assigned_to_another_product(self):
        Product.objects.create(
            name='Bread',
            category='Bakery',
            price=Decimal('8.75'),
            inventory_level=12,
            lcd_id=1,
        )

        response = self.client.get(reverse('lcd_management'))
        milk = next(
            product for product in response.context['products']
            if product.id == self.product.id
        )

        self.assertEqual(milk.assignment_lcd_ids, [2, 3])

    def test_assigned_products_are_listed_before_unassigned_products(self):
        Product.objects.create(
            name='Bread',
            category='Bakery',
            price=Decimal('8.75'),
            inventory_level=12,
            lcd_id=1,
        )
        Product.objects.create(
            name='Apples',
            category='Produce',
            price=Decimal('5.25'),
            inventory_level=18,
            lcd_id=0,
        )

        response = self.client.get(reverse('lcd_management'))
        product_names = [product.name for product in response.context['products']]

        self.assertEqual(product_names, ['Bread', 'Milk', 'Apples'])

    def test_unassigned_products_render_without_lcd_zero(self):
        self.product.lcd_id = 0
        self.product.save(update_fields=['lcd_id'])

        response = self.client.get(reverse('lcd_management'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Not assigned')
        self.assertNotContains(response, 'LCD 0')

    @patch('ProjectSmartShelf.views.send_price_update', return_value=True)
    def test_sync_all_publishes_current_product_prices_and_skips_unassigned(self, mock_send_price_update):
        Product.objects.create(
            name='Bread',
            category='Bakery',
            price=Decimal('8.75'),
            inventory_level=12,
            lcd_id=1,
        )
        Product.objects.create(
            name='Apples',
            category='Produce',
            price=Decimal('5.25'),
            inventory_level=18,
            lcd_id=0,
        )

        response = self.client.post(reverse('lcd_management'), {'action': 'sync_all'})

        self.assertRedirects(response, reverse('lcd_management'))
        self.assertEqual(mock_send_price_update.call_count, 2)
        mock_send_price_update.assert_any_call(2, 'Milk', '12.50')
        mock_send_price_update.assert_any_call(1, 'Bread', '8.75')

    @patch('ProjectSmartShelf.views.send_price_update', return_value=True)
    @patch('ProjectSmartShelf.utils.send_price_update', return_value=True)
    def test_sync_all_marks_active_promotions_on_lcd(self, mock_model_send, mock_view_send):
        today = timezone.localdate()
        ScheduledPromotion.objects.create(
            product=self.product,
            title='Shelf sale',
            promotional_price=Decimal('9.99'),
            start_date=today,
            end_date=today,
        )
        ScheduledPromotion.process_due_promotions(today)
        mock_view_send.reset_mock()

        response = self.client.post(reverse('lcd_management'), {'action': 'sync_all'})

        self.assertRedirects(response, reverse('lcd_management'))
        mock_view_send.assert_called_once_with(
            2,
            'Milk',
            '9.99',
            promotion_label='PROMO',
        )
