"""
Serializers for Learner Home
"""

from datetime import date, timedelta
from urllib.parse import urlencode, urljoin

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from opaque_keys.edx.keys import CourseKey
from rest_framework import serializers
from openedx_filters.learning.filters import CourseEnrollmentAPIRenderStarted, CourseRunAPIRenderStarted

from common.djangoapps.course_modes.models import CourseMode
from openedx.features.course_experience import course_home_url
from xmodule.data import CertificatesDisplayBehaviors
from lms.djangoapps.learner_home.utils import course_progress_url
from access_subscriptions.models import UserSubscription, Subscription
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
import logging
logger = logging.getLogger(__name__)


class LiteralField(serializers.Field):
    """
    Custom Field for use with fields that will always intentionally serialize to the same static value.
    """

    def __init__(self, literal_value):
        super().__init__()
        self.literal_value = literal_value

    def to_representation(self, _):
        return self.literal_value

    def get_attribute(self, _):
        return self.literal_value


class PlatformSettingsSerializer(serializers.Serializer):
    """Serializer for platform-level info, emails, and URLs"""

    supportEmail = serializers.EmailField()
    billingEmail = serializers.EmailField()
    courseSearchUrl = serializers.URLField()


class SocialMediaSiteSettingsSerializer(serializers.Serializer):
    """Social media sharing config for a particular website"""

    isEnabled = serializers.BooleanField(source="is_enabled")
    socialBrand = serializers.CharField(source="brand")
    utmParams = serializers.CharField(source="utm_params")


class SocialShareSettingsSerializer(serializers.Serializer):
    """Serializer for social media sharing config"""

    facebook = SocialMediaSiteSettingsSerializer()
    twitter = SocialMediaSiteSettingsSerializer()


class CourseProviderSerializer(serializers.Serializer):
    """Info about a course provider (institution/business) from a CourseOverview"""

    name = serializers.CharField(source="display_org_with_default")


class CourseSerializer(serializers.Serializer):
    """Course header information, derived from a CourseOverview"""

    requires_context = True

    bannerImgSrc = serializers.URLField(source="image_urls.small")
    courseName = serializers.CharField(source="display_name_with_default")
    courseNumber = serializers.CharField(source="display_number_with_default")
    socialShareUrl = serializers.SerializerMethodField()

    def get_socialShareUrl(self, instance):
        return self.context.get("course_share_urls", {}).get(instance.id)


class CourseRunSerializer(serializers.Serializer):
    """
    Information about a course run.
    Derived from the CourseEnrollment with required context:
    - "resume_course_urls" (dict) with a matching course_id key
    - "ecommerce_payment_page" (url) root to the ecommerce page
    - "course_mode_info" (dict) keyed by course ID, with sub info:
        - "verified_sku" (uid, optional) if the course has an upgrade identifier
        - "days_for_upsell" (int, optional) days before audit student loses access
    """

    requires_context = True

    isStarted = serializers.SerializerMethodField()
    isArchived = serializers.SerializerMethodField()
    courseId = serializers.CharField(source="course_id")
    minPassingGrade = serializers.DecimalField(
        max_digits=5, decimal_places=2, source="course_overview.lowest_passing_grade"
    )
    startDate = serializers.DateTimeField(source="course_overview.start")
    endDate = serializers.DateTimeField(source="course_overview.end")
    homeUrl = serializers.SerializerMethodField()
    marketingUrl = serializers.URLField(
        source="course_overview.marketing_url", allow_null=True
    )
    progressUrl = serializers.SerializerMethodField()
    unenrollUrl = serializers.SerializerMethodField()
    upgradeUrl = serializers.SerializerMethodField()
    resumeUrl = serializers.SerializerMethodField()

    def get_isStarted(self, instance):
        return instance.course_overview.has_started()

    def get_isArchived(self, instance):
        return instance.course_overview.has_ended()

    def get_homeUrl(self, instance):
        return course_home_url(instance.course_id)

    def get_progressUrl(self, instance):
        return course_progress_url(instance.course_id)

    def get_unenrollUrl(self, instance):
        return reverse("course_run_refund_status", args=[instance.course_id])

    def get_upgradeUrl(self, instance):
        """If the enrollment mode has a verified upgrade through ecommerce, return the link"""
        ecommerce_payment_page = self.context.get("ecommerce_payment_page")
        verified_sku = (
            self.context.get("course_mode_info", {})
            .get(instance.course_id, {})
            .get("verified_sku")
        )

        if ecommerce_payment_page and verified_sku:
            query_params = {
                'sku': verified_sku,
                'course_run_key': str(instance.course_id)
            }
            encoded_params = urlencode(query_params)
            upgrade_url = f"{ecommerce_payment_page}?{encoded_params}"
            return upgrade_url

    def get_resumeUrl(self, instance):
        return self.context.get("resume_course_urls", {}).get(instance.course_id)

    def to_representation(self, instance):
        """Serialize the courserun instance to be able to update the values before the API finishes rendering."""
        serialized_courserun = super().to_representation(instance)
        serialized_courserun = CourseRunAPIRenderStarted().run_filter(
            serialized_courserun=serialized_courserun,
        )
        return serialized_courserun


class CoursewareAccessSerializer(serializers.Serializer):
    """
    Info determining whether a user should be able to view course material.
    Mirrors logic in 'show_courseware_links_for' from old dashboard.py
    """
    requires_context = True

    hasUnmetPrerequisites = serializers.SerializerMethodField()
    isTooEarly = serializers.SerializerMethodField()
    isStaff = serializers.SerializerMethodField()
    lacksSubscription = serializers.SerializerMethodField()
    hasActiveSubscription = serializers.SerializerMethodField()

    def _get_course_access_checks(self, enrollment):
        """Internal helper to unpack access object for this particular enrollment"""
        return self.context.get("course_access_checks", {}).get(enrollment.course_id, {})

    def get_hasUnmetPrerequisites(self, enrollment):
        user = self.context.get('user')
        if not user:
            logger.error(f"CoursewareAccessSerializer: No user in context for enrollment {enrollment.course_id}")
            return True  # Default to restrictive access
        if user.is_authenticated and (user.is_superuser or user.is_staff or self._get_course_access_checks(enrollment).get("user_has_staff_access", False)):
            logger.debug(f"User {user.username} (ID: {user.id}) is staff or superuser, no prerequisites required for course {enrollment.course_id}")
            return False
        has_prereqs = self._get_course_access_checks(enrollment).get("has_unmet_prerequisites", False)
        logger.debug(f"User {user.username} (ID: {user.id}) has_unmet_prerequisites={has_prereqs} for course {enrollment.course_id}")
        return has_prereqs

    def get_isTooEarly(self, enrollment):
        user = self.context.get('user')
        if not user:
            logger.error(f"CoursewareAccessSerializer: No user in context for enrollment {enrollment.course_id}")
            return True  # Default to restrictive access
        if user.is_authenticated and (user.is_superuser or user.is_staff or self._get_course_access_checks(enrollment).get("user_has_staff_access", False)):
            logger.debug(f"User {user.username} (ID: {user.id}) is staff or superuser, bypassing start date check for course {enrollment.course_id}")
            return False
        is_too_early = self._get_course_access_checks(enrollment).get("is_too_early_to_view", False)
        logger.debug(f"User {user.username} (ID: {user.id}) is_too_early={is_too_early} for course {enrollment.course_id}")
        return is_too_early

    def get_isStaff(self, enrollment):
        user = self.context.get('user')
        if not user:
            logger.error(f"CoursewareAccessSerializer: No user in context for enrollment {enrollment.course_id}")
            return False  # Default to non-staff
        is_staff = user.is_authenticated and (user.is_superuser or user.is_staff or self._get_course_access_checks(enrollment).get("user_has_staff_access", False))
        logger.debug(f"User {user.username} (ID: {user.id}) is_staff={is_staff} for course {enrollment.course_id}")
        return is_staff

    def get_lacksSubscription(self, enrollment):
        user = self.context.get('user')
        if not user:
            logger.error(f"CoursewareAccessSerializer: No user in context for enrollment {enrollment.course_id}")
            return True  # Default to requiring subscription
        if user.is_authenticated:
            if user.is_superuser or user.is_staff or self._get_course_access_checks(enrollment).get("user_has_staff_access", False):
                logger.debug(f"User {user.id} (username: {user.username}) is staff or superuser, skipping subscription check for course {enrollment.course_id}")
                return False
            subscription = UserSubscription.objects.filter(
                user=user,
                is_active=True,
                end_date__gte=timezone.now()
            ).select_related('subscription').first()
            if not subscription:
                logger.debug(f"No active subscription found for user {user.id} (username: {user.username}) for course {enrollment.course_id}")
                return True
            if subscription.subscription.course_access_type == 'Specific':
                if not subscription.subscription.specific_courses.filter(id=enrollment.course_id).exists():
                    logger.debug(f"Course {enrollment.course_id} not in specific subscription for user {user.id} (username: {user.username})")
                    return True
            logger.debug(f"Active subscription found for user {user.id} (username: {user.username}): end_date={subscription.end_date} for course {enrollment.course_id}")
            return False
        logger.debug(f"User is not authenticated, setting lacksSubscription to True for course {enrollment.course_id}")
        return True

    def get_hasActiveSubscription(self, enrollment):
        lacks_subscription = self.get_lacksSubscription(enrollment)
        has_active = not lacks_subscription
        user = self.context.get('user')
        if user:
            logger.debug(f"User {user.username} (ID: {user.id}) has_active_subscription={has_active} for course {enrollment.course_id}")
        return has_active


class EnrollmentSerializer(serializers.Serializer):
    """
    Info about this particular enrollment.
    """
    requires_context = True

    accessExpirationDate = serializers.SerializerMethodField()
    isAudit = serializers.SerializerMethodField()
    hasStarted = serializers.SerializerMethodField()
    coursewareAccess = CoursewareAccessSerializer(source="*")
    isVerified = serializers.SerializerMethodField()
    canUpgrade = serializers.SerializerMethodField()
    isAuditAccessExpired = serializers.SerializerMethodField()
    isEmailEnabled = serializers.SerializerMethodField()
    hasOptedOutOfEmail = serializers.SerializerMethodField()
    lastEnrolled = serializers.DateTimeField(source="created")
    isEnrolled = serializers.BooleanField(source="is_active")
    mode = serializers.CharField()
    lacksSubscription = serializers.SerializerMethodField()
    hasActiveSubscription = serializers.SerializerMethodField()
    isSubscriptionExpired = serializers.SerializerMethodField()

    def get_accessExpirationDate(self, instance):
        return self.context.get("audit_access_deadlines", {}).get(instance.course_id)

    def get_isAudit(self, enrollment):
        return enrollment.mode in CourseMode.AUDIT_MODES

    def get_hasStarted(self, enrollment):
        resume_button_url = self.context.get("resume_course_urls", {}).get(enrollment.course_id)
        return bool(resume_button_url)

    def get_isVerified(self, enrollment):
        return enrollment.is_verified_enrollment()

    def get_canUpgrade(self, enrollment):
        use_ecommerce_payment_flow = bool(self.context.get("ecommerce_payment_page"))
        course_mode_info = self.context.get("course_mode_info", {}).get(enrollment.course_id, {})
        return bool(
            use_ecommerce_payment_flow
            and course_mode_info.get("show_upsell", False)
            and course_mode_info.get("verified_sku", False)
        )

    def get_isAuditAccessExpired(self, enrollment):
        expiration_date = self.context.get("audit_access_deadlines", {}).get(enrollment.course_id)
        return bool(expiration_date) and timezone.now() > expiration_date

    def get_isEmailEnabled(self, enrollment):
        return enrollment.course_id in self.context.get("show_email_settings_for", [])

    def get_hasOptedOutOfEmail(self, enrollment):
        return enrollment.course_id in self.context.get("course_optouts", [])

    def get_lacksSubscription(self, enrollment):
        user = self.context.get('user')
        if not user:
            logger.error(f"EnrollmentSerializer: No user in context for enrollment {enrollment.course_id}")
            return True
        courseware_access = CoursewareAccessSerializer(enrollment, context=self.context).data
        lacks_subscription = courseware_access.get('lacksSubscription', True)
        logger.debug(f"User {user.username} (ID: {user.id}) lacks_subscription={lacks_subscription} for course {enrollment.course_id}")
        return lacks_subscription

    def get_hasActiveSubscription(self, enrollment):
        user = self.context.get('user')
        if not user:
            logger.error(f"EnrollmentSerializer: No user in context for enrollment {enrollment.course_id}")
            return False
        courseware_access = CoursewareAccessSerializer(enrollment, context=self.context).data
        has_active = courseware_access.get('hasActiveSubscription', False)
        logger.debug(f"User {user.username} (ID: {user.id}) has_active_subscription={has_active} for course {enrollment.course_id}")
        return has_active

    def get_isSubscriptionExpired(self, enrollment):
        has_active = self.get_hasActiveSubscription(enrollment)
        is_expired = not has_active
        user = self.context.get('user')
        if user:
            logger.debug(f"User {user.username} (ID: {user.id}) is_subscription_expired={is_expired} for course {enrollment.course_id}")
        return is_expired

    def to_representation(self, instance):
        serialized_enrollment = super().to_representation(instance)
        course_key, serialized_enrollment = CourseEnrollmentAPIRenderStarted().run_filter(
            course_key=instance.course_id,
            serialized_enrollment=serialized_enrollment,
        )
        return serialized_enrollment


class GradeDataSerializer(serializers.Serializer):
    """Info about grades for this enrollment"""

    requires_context = True

    isPassing = serializers.SerializerMethodField()

    def get_isPassing(self, enrollment):
        return self.context.get("grade_statuses", {}).get(enrollment.course_id, False)


class CertificateSerializer(serializers.Serializer):
    """Certificate availability info"""

    requires_context = True

    availableDate = serializers.SerializerMethodField()
    isRestricted = serializers.SerializerMethodField()
    isEarned = serializers.SerializerMethodField()
    isDownloadable = serializers.SerializerMethodField()
    certPreviewUrl = serializers.SerializerMethodField()

    def get_cert_info(self, enrollment):
        """Utility to grab certificate info for this enrollment or empty object"""
        return self.context.get("cert_statuses", {}).get(enrollment.course.id, {})

    def get_availableDate(self, enrollment):
        """Available date changes based off of Certificate display behavior"""
        course_overview = enrollment.course_overview
        available_date = None

        if (
            course_overview.certificates_display_behavior
            == CertificatesDisplayBehaviors.END_WITH_DATE
            and course_overview.certificate_available_date
        ):
            available_date = course_overview.certificate_available_date
        elif (
            course_overview.certificates_display_behavior
            == CertificatesDisplayBehaviors.END
            and course_overview.end
        ):
            available_date = course_overview.end

        return serializers.DateTimeField().to_representation(available_date)

    def get_isRestricted(self, enrollment):
        """Cert is considered restricted based on certificate status"""
        return self.get_cert_info(enrollment).get("status") == "restricted"

    def get_isEarned(self, enrollment):
        """Cert is considered earned based on certificate status"""
        is_earned_states = ("downloadable", "certificate_earned_but_not_available")
        return self.get_cert_info(enrollment).get("status") in is_earned_states

    def get_isDownloadable(self, enrollment):
        """Cert is considered downloadable based on certificate status"""
        return self.get_cert_info(enrollment).get("status") == "downloadable"

    def get_certPreviewUrl(self, enrollment):
        """Cert preview URL comes from certificate info"""
        cert_info = self.get_cert_info(enrollment)
        if not cert_info.get("show_cert_web_view", False):
            return None
        else:
            return cert_info.get("cert_web_view_url")


class AvailableEntitlementSessionSerializer(serializers.Serializer):
    """An available entitlement session"""

    startDate = serializers.DateTimeField(source="start")
    endDate = serializers.DateTimeField(source="end")
    courseId = serializers.CharField(source="key")


class EntitlementSerializer(serializers.Serializer):
    """Entitlement info"""

    requires_context = True

    availableSessions = serializers.SerializerMethodField()
    uuid = serializers.UUIDField()
    isRefundable = serializers.BooleanField(source="is_entitlement_refundable")
    isFulfilled = serializers.SerializerMethodField()
    changeDeadline = serializers.SerializerMethodField()
    isExpired = serializers.SerializerMethodField()
    expirationDate = serializers.SerializerMethodField()

    # DRF doesn't convert None to False so we must do this rather than a booleanfield:
    # https://github.com/encode/django-rest-framework/issues/2299
    def get_isFulfilled(self, instance):
        return bool(instance.enrollment_course_run)

    def get_isExpired(self, instance):
        return bool(instance.expired_at)

    def get_availableSessions(self, instance):
        availableSessions = self.context["course_entitlement_available_sessions"].get(
            str(instance.uuid)
        )
        return AvailableEntitlementSessionSerializer(availableSessions, many=True).data

    def get_expirationDate(self, instance):
        if instance.expired_at is not None:
            return instance.expired_at
        else:
            return date.today() + timedelta(days=instance.get_days_until_expiration())

    def get_changeDeadline(self, instance):
        return self.get_expirationDate(instance)


class RelatedProgramSerializer(serializers.Serializer):
    """Related programs information"""

    bannerImgSrc = serializers.URLField(source="banner_image.small.url", default=None)
    logoImgSrc = serializers.SerializerMethodField()
    numberOfCourses = serializers.SerializerMethodField()
    programType = serializers.CharField(source="type")
    programUrl = serializers.SerializerMethodField()
    provider = serializers.SerializerMethodField()
    title = serializers.CharField()

    def get_numberOfCourses(self, instance):
        return len(instance["courses"])

    def get_logoImgSrc(self, instance):
        return (
            instance["authoring_organizations"][0].get("logo_image_url")
            if instance.get("authoring_organizations")
            else None
        )

    def get_provider(self, instance):
        return (
            instance["authoring_organizations"][0].get("name")
            if instance.get("authoring_organizations")
            else None
        )

    def get_programUrl(self, instance):
        return urljoin(
            settings.LMS_ROOT_URL,
            instance.get(
                "detail_url",
                reverse(
                    "program_details_view", kwargs={"program_uuid": instance["uuid"]}
                ),
            ),
        )


class ProgramsSerializer(serializers.Serializer):
    """Programs information"""

    relatedPrograms = serializers.ListField(
        child=RelatedProgramSerializer(), allow_empty=True
    )


class CreditSerializer(serializers.Serializer):
    """Credit status information"""

    providerStatusUrl = serializers.URLField(source="provider_status_url")
    providerName = serializers.CharField(source="provider_name")
    providerId = serializers.CharField(source="provider_id")
    error = serializers.BooleanField()
    purchased = serializers.BooleanField()
    requestStatus = serializers.CharField(source="request_status")


class LearnerEnrollmentSerializer(serializers.Serializer):
    """
    Info for displaying an enrollment on the learner dashboard.
    Derived from a CourseEnrollment with added context.
    """

    requires_context = True

    course = CourseSerializer()
    courseProvider = CourseProviderSerializer(source="course_overview")
    courseRun = CourseRunSerializer(source="*")
    enrollment = EnrollmentSerializer(source="*")
    certificate = CertificateSerializer(source="*")
    entitlement = serializers.SerializerMethodField()
    gradeData = GradeDataSerializer(source="*")
    programs = serializers.SerializerMethodField()
    credit = serializers.SerializerMethodField()

    def get_entitlement(self, instance):
        """
        If this enrollment is the fulfillment of an entitlement, include information about the entitlement
        """
        entitlement = self.context["fulfilled_entitlements"].get(
            str(instance.course_id)
        )
        if entitlement:
            return EntitlementSerializer(entitlement, context=self.context).data
        else:
            return {}

    def get_programs(self, instance):
        """
        If this enrollment is part of a program, include information about the program and related programs
        """
        programs = self.context["programs"].get(str(instance.course_id), [])
        return ProgramsSerializer(
            {"relatedPrograms": programs}, context=self.context
        ).data

    def get_credit(self, instance):
        """Pull credit statuses from context"""
        credit_status = self.context["credit_statuses"].get(instance.course_id)

        # If user or course is ineligible for credit, return empty
        if not credit_status:
            return {}
        else:
            return CreditSerializer(credit_status).data


class UnfulfilledEntitlementSerializer(serializers.Serializer):
    """
    Serializer for an unfulfilled entitlement.
    This should have the same keys as the LearnerEnrollmentSerializer.
    We are flattening the two lists into one "course card" list and so should be the same shape.
    """

    requires_context = True

    # This is the static constant data returned as the 'enrollment' key for all unfulfilled enrollments.
    STATIC_ENTITLEMENT_ENROLLMENT_DATA = {
        "accessExpirationDate": None,
        "isAudit": False,
        "hasStarted": False,
        "coursewareAccess": {
            "hasUnmetPrerequisites": False,
            "isTooEarly": False,
            "isStaff": False,
        },
        "isVerified": False,
        "canUpgrade": False,
        "isAuditAccessExpired": False,
        "isEmailEnabled": False,
        "hasOptedOutOfEmail": False,
        "lastEnrolled": None,
        "isEnrolled": False,
        "mode": None,
    }

    # These fields contain all real data and will be serialized
    entitlement = EntitlementSerializer(source="*")
    course = serializers.SerializerMethodField()
    courseProvider = serializers.SerializerMethodField()
    programs = serializers.SerializerMethodField()

    # These fields are literal values that do not change
    courseRun = LiteralField(None)
    gradeData = LiteralField(None)
    certificate = LiteralField(None)
    enrollment = LiteralField(STATIC_ENTITLEMENT_ENROLLMENT_DATA)
    credit = LiteralField({})

    def _get_course_overview(self, instance):
        """Look up course provider from CourseOverview matching the pseudo session"""
        pseudo_session = self.context["unfulfilled_entitlement_pseudo_sessions"].get(
            str(instance.uuid)
        )
        if pseudo_session:
            course_key = CourseKey.from_string(pseudo_session["key"])
            return self.context.get("pseudo_session_course_overviews").get(course_key)

    def get_course(self, instance):
        """Serialize course info from a course overview"""
        course_overview = self._get_course_overview(instance)
        return CourseSerializer(course_overview, context=self.context).data

    def get_courseProvider(self, instance):
        """Serialize course provider info from a course overview"""
        course_overview = self._get_course_overview(instance)
        return CourseProviderSerializer(course_overview, allow_null=True).data

    def get_programs(self, instance):
        """
        If this entitlement is part of a program, include information about the program and related programs
        """
        programs = self.context["programs"].get(str(instance.course_uuid), [])
        return ProgramsSerializer(
            {"relatedPrograms": programs}, context=self.context
        ).data


class SuggestedCourseSerializer(serializers.Serializer):
    """Serializer for a suggested course from recommendation engine"""

    bannerImgSrc = serializers.URLField(source="logo_image_url")
    logoImgSrc = serializers.URLField(allow_null=True)
    courseName = serializers.CharField(source="title")
    courseUrl = serializers.URLField(source="marketing_url")


class EmailConfirmationSerializer(serializers.Serializer):
    """Serializer for email confirmation banner resources"""

    isNeeded = serializers.BooleanField()
    sendEmailUrl = serializers.URLField()


class EnterpriseDashboardSerializer(serializers.Serializer):
    """Serializer for individual enterprise dashboard data"""

    label = serializers.CharField(source="name")
    url = serializers.SerializerMethodField()
    uuid = serializers.UUIDField()
    authOrgId = serializers.CharField(source="auth_org_id", allow_null=True)
    isLearnerPortalEnabled = serializers.BooleanField(source="enable_learner_portal")

    def get_url(self, instance):
        return urljoin(
            settings.ENTERPRISE_LEARNER_PORTAL_BASE_URL,
            instance["slug"],
        )


class LearnerDashboardSerializer(serializers.Serializer):
    """Serializer for all info required to render the Learner Dashboard"""

    requires_context = True

    emailConfirmation = EmailConfirmationSerializer()
    enterpriseDashboard = EnterpriseDashboardSerializer(allow_null=True)
    platformSettings = PlatformSettingsSerializer()
    courses = serializers.SerializerMethodField()
    socialShareSettings = SocialShareSettingsSerializer()
    suggestedCourses = serializers.ListField(
        child=SuggestedCourseSerializer(), allow_empty=True
    )

    def get_courses(self, instance):
        """
        Get a list of course cards by serializing enrollments and entitlements into
        a single list.
        """
        courses = []

        for enrollment in instance.get("enrollments", []):
            courses.append(
                LearnerEnrollmentSerializer(enrollment, context=self.context).data
            )
        for entitlement in instance.get("unfulfilledEntitlements", []):
            courses.append(
                UnfulfilledEntitlementSerializer(entitlement, context=self.context).data
            )

        return courses
