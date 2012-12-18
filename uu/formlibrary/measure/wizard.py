import copy

from plone.app.uuid.utils import uuidToObject
from plone.autoform.form import AutoExtensibleForm
from plone.directives import form
from plone.formwidget.contenttree import UUIDSourceBinder
from plone.formwidget.contenttree import ContentTreeFieldWidget
from plone.keyring.interfaces import IKeyManager
from plone.z3cform.interfaces import IWrappedForm
from plone.z3cform.traversal import FormWidgetTraversal
from Products.statusmessages.interfaces import IStatusMessage
from z3c.form import form as z3cform
from z3c.form import button
from z3c.form.interfaces import HIDDEN_MODE
from z3c.form.browser.radio import RadioFieldWidget
from zope.component import queryUtility, adapts
from zope.component.hooks import getSite
from zope.interface import Interface, alsoProvides, implements
from zope.publisher.interfaces.browser import IBrowserRequest
from zope import schema
from zope.schema import getFieldsInOrder
from zope.schema.interfaces import IContextSourceBinder
from zope.schema.vocabulary import SimpleVocabulary, SimpleTerm
from zope.traversing.interfaces import ITraversable

from uu.formlibrary.interfaces import DEFINITION_TYPE
from uu.formlibrary.interfaces import SIMPLE_FORM_TYPE, MULTI_FORM_TYPE
from uu.formlibrary.interfaces import IFormComponents

from utils import SignedPickleIO


class IMeasureWizardSubform(form.Schema):
    """Marker base for a subform of the measure creation wizard"""


class IMeasureWizardNaming(IMeasureWizardSubform):
    """Title, description subform"""
    
    title = schema.TextLine(
        title=u'Name (title) of measure',
        required=True,
        )
    
    description = schema.Text(
        title=u'Describe your measure (optional)',
        required=False,
        )


class IMeasureWizardDefinition(IMeasureWizardSubform):
    """
    Subform schema: choose form definition for use in measure filters or
    field specifiers used for data-binding.
    """
    
    form.widget(definition=ContentTreeFieldWidget)
    definition = schema.Choice(
        title=u'Select a form definition',
        description=u'Select a form definition to use for this measure. '\
                    u'The definition that you choose will control the '\
                    u'available fields for query by this measure.',
        source=UUIDSourceBinder(portal_type=DEFINITION_TYPE),
        )


FORM_TYPE_CHOICES = SimpleVocabulary([
    SimpleTerm(SIMPLE_FORM_TYPE, title=u'Flex form'),
    SimpleTerm(MULTI_FORM_TYPE, title=u'Multi-record form'),
])

class IMeasureWizardSourceType(IMeasureWizardSubform):
    """Choose form data-source type (branch point in wizard decision tree)"""
    
    form.widget(form_type=RadioFieldWidget)
    form_type = schema.Choice(
        title=u'Form type',
        description=u'What kind of form data will provide measure values?',
        vocabulary=FORM_TYPE_CHOICES,
        default=MULTI_FORM_TYPE,
        ) 


MR_FORM_FILTER_CHOICES = SimpleVocabulary([
    SimpleTerm('constant', title=u'Constant value (1)'),
    SimpleTerm('multi_total', title=u'Total records (multi-record form)'),
    SimpleTerm(
        'multi_filter',
        title=u'Value computed by a filter of records',
        )
])


class IMeasureWizardMRCriteria(IMeasureWizardSubform):
    """Numerator/denominator selection for measure via multi-record form"""
    
    form.widget(numerator=RadioFieldWidget)
    numerator = schema.Choice(
        title=u'Computed value: numerator',
        description=u'Choose how the core computed value is obtained.',
        vocabulary=MR_FORM_FILTER_CHOICES,
        default='multi_filter',
        )
    
    form.widget(denominator=RadioFieldWidget)
    denominator = schema.Choice(
        title=u'(Optional) denominator',
        description=u'You may choose if and how an optional denominator is '\
                    u'used to compute this measure.  The default '\
                    u'denominator is the total of records for a given form, '\
                    u'which is useful for percentages and ratios, but you '\
                    u'may also choose a constant denominator if what you '\
                    u'aim to compute and display is a raw count of matches '\
                    u'resulting from a numerator computed by filter.',
        vocabulary=MR_FORM_FILTER_CHOICES,
        default='multi_total',
        )


VALUE_TYPE_CHOICES = SimpleVocabulary([
    SimpleTerm(value=v, title=title)
        for v, title in (
            ('count', u'Count of occurrences'),
            ('decimal', u'Decimal number, including ratio value.'),
            ('percentage', u'Percentage of total or selected records'),
            ('rating', u'Rating on a scale'),
            )
    ]
)


ROUNDING_CHOICES = SimpleVocabulary([
    SimpleTerm('', title=u'No rounding'),
    SimpleTerm('round', title=u'Round (up/down)'),
    SimpleTerm('ceiling', title=u'Ceiling: always round upward.'),
    SimpleTerm('floor', title=u'Floor: always round downward.'),
])


class IMeasureWizardMRUnits(IMeasureWizardSubform):
    """Multi-record value modifiers: units, multiplier, rounding rules"""
    
    form.widget(value_type=RadioFieldWidget)
    value_type = schema.Choice(
        title=u'Kind of value',
        description=u'What are the basic units of measure?',
        vocabulary=VALUE_TYPE_CHOICES,
        )
    
    multiplier = schema.Float(
        title=u'Value multiplier constant',
        description=u'What constant numeric (whole or decimal number) '\
                    u'should the raw computed value be multiplied by?  For '\
                    u'percentages computed with both a numerator and '\
                    u'denominator, enter 100.',
        default=1.0,
        )
    
    units = schema.TextLine(
        title=u'Units of measure',
        description=u'Label for units of measure (optional).',
        required=False,
        )
    
    form.widget(rounding=RadioFieldWidget)
    rounding = schema.Choice(
        title=u'(optional) Rounding rule',
        description=u'You may choose to round decimal values to integer '\
                    u'(whole or negative non-decimal number) values using '\
                    u'a rounding rule; be default, no rounding takes place.',
        vocabulary=ROUNDING_CHOICES,
        required=False,
        default=None,
        )
    
    display_precision = schema.Int(
        title=u'Display precision',
        description=u'When displaying a decimal value, how many places '\
                    u'beyond the decimal point should be displayed in '\
                    u'output?  Default: two digits after the decimal point.',
        default=2,
        )


def _source_definition(context):
    all_data = context.get('all_data', None)
    if not all_data:
        return None
    definition_data = all_data.get('IMeasureWizardDefinition', None)
    defn_uid = definition_data.get('definition', None)
    if defn_uid is None:
        return None
    defn = uuidToObject(defn_uid)
    return defn


def _source_fieldset(context):
    all_data = context.get('all_data', None)
    if all_data:
        fieldset_data = all_data.get('IMeasureWizardFlexFieldsetChoice', None)
        if fieldset_data:
            return fieldset_data.get('fieldset')
    return None


def fieldname_choices(context):
    definition = _source_definition(context)
    if definition is None:
        return SimpleVocabulary([])
    fieldset = _source_fieldset(context)
    schema = definition.schema  # default fieldset
    if fieldset is not None:
        components = IFormComponents(definition)
        if fieldset in components.names:
            schema = components.groups.get(fieldset).schema
    _term = lambda info: SimpleTerm(info[0], title=info[1].title)
    return SimpleVocabulary(map(_term, getFieldsInOrder(schema)))


alsoProvides(fieldname_choices, IContextSourceBinder)

def fieldset_choices(context):
    definition = _source_definition(context)
    if definition is None:
        return SimpleVocabulary([])
    components = IFormComponents(definition)
    _term = lambda o: SimpleTerm(o.getId(), title=o.Title())
    choices = list(components.groups.values())
    default = SimpleTerm('', title=u'Default fieldset')
    return SimpleVocabulary([default] + map(_term, choices)) 


alsoProvides(fieldset_choices, IContextSourceBinder)


class IMeasureWizardFlexFieldsetChoice(IMeasureWizardSubform):
    """
    Choose a fieldset from which to obtain value from Flex form.
    """
    
    fieldset = schema.Choice(
        title=u'Choose a field group from which to find a field name',
        source=fieldset_choices,
        required=True,
        default=None,
        )


class IMeasureWizardFlexFieldChoice(IMeasureWizardSubform):
    """
    Choose a field or fieldset from which to obtain value from Flex form.
    Assumes a context of a form definition.
    """
    
    fieldname = schema.Choice(
        title=u'Choose a fieldname (primary schema)',
        source=fieldname_choices,
        required=False,
        )


delta_tables = { 
    IMeasureWizardNaming : {
        'next' : IMeasureWizardDefinition, 
        },  
    IMeasureWizardDefinition : {
        'previous' : IMeasureWizardNaming,
        'next' : IMeasureWizardSourceType,
        },  
    IMeasureWizardSourceType : {
        'previous' : IMeasureWizardDefinition,
        'branch_flex' : IMeasureWizardFlexFieldsetChoice,
        'branch_mr' : IMeasureWizardMRCriteria,
        },  
    IMeasureWizardMRCriteria : {
        'previous' : IMeasureWizardSourceType,
        'next' : IMeasureWizardMRUnits,
        },  
    IMeasureWizardMRUnits : {
        'previous' : IMeasureWizardMRCriteria,
        'next' : None,   # final
        },  
    IMeasureWizardFlexFieldsetChoice : {
        'previous' : IMeasureWizardSourceType,
        'next' : IMeasureWizardFlexFieldChoice,
        },  
    IMeasureWizardFlexFieldChoice : {
        'previous' : IMeasureWizardFlexFieldsetChoice,
        'next' : None,   # final
        },  
    }


BUTTON_LABELS = {
    'next' : u'Next >>',
    'previous' : u'<< Previous',
    'cancel' : u'Cancel',
}

USE_DEFINITION_CONTEXT = (IMeasureWizardFlexFieldChoice,)

STEP_TITLES = {
    IMeasureWizardNaming : u'Name and describe your measure',
    IMeasureWizardDefinition : u'Choose a form definition for the measure',
    IMeasureWizardSourceType : u'What type of data source?',
    IMeasureWizardMRCriteria : u'How is measure calculated',
    IMeasureWizardMRUnits : 'Describe and configure the units of measure?',
    IMeasureWizardFlexFieldChoice : u'Choose a field as a value source.',
    IMeasureWizardFlexFieldsetChoice : u'Choose a field group from which a '\
                                       u'form field will be chosen.',
}

class IStepState(Interface):
    source_step = schema.BytesLine(
        title=u'Source step name',
        description=u'Interface class name for source step (hidden input).',
        required=False,
        )

STEP_STATE_KEY = 'form.widgets.IStepState.source_step'


def flex_context_hook(context, schema, data):
    if data is None or schema not in (IMeasureWizardFlexFieldChoice,):
        return None
    definition_data = data.get('IMeasureWizardDefinition')
    defn_uid = definition_data.get('definition')
    defn = uuidToObject(defn_uid)
    if defn is None:
        raise ValueError('Could not obtain definition')
    return defn


class WizardStepForm(AutoExtensibleForm, z3cform.Form):
    """
    Form for a content creation wizard step, always with some context.
    """
   
    implements(IWrappedForm)
    autoGroups = False
     
    # schema must be property, not attribute for AutoExtensibleForm sublcass
    @property
    def schema(self):
        return self._schema 
    
    @property
    def additionalSchemata(self):
        return (IStepState,)

    def __init__(self, context, request, schema, data=None):
        self._schema = schema
        self.context = context
        self.__parent__ = context   # zope2 security permission acquisition
        self.request = request
        self.data = data
    
    def updateWidgets(self):
        super(WizardStepForm, self).updateWidgets()
        if 'IStepState.source_step' in self.fields:
            # inject value into hidden field via request:
            self.request.form[STEP_STATE_KEY] = self.schema.__name__
            self.request.other[STEP_STATE_KEY] = self.schema.__name__
            # make sure that the widget is hidden input, and that above
            # injections are considered:
            widget = self.widgets['IStepState.source_step']
            widget.mode = HIDDEN_MODE
            widget.value = widget.extract()  # force get value from request
    
    def getContent(self):
        """
        Get content for use as form context by form widgets.  Note that
        this will be the context used by context source binder functions
        and as a result anything that needs to be passed to the source
        binder should be passed through self.data on construction of 
        a WizardStepForm.
        """
        if self.data:
            return dict(self.data)
        return {}


class IMeasureWizardView(Interface):
    """marker for view, used for traversal adapter"""


class MeasureWizardView(object):
    
    implements(IMeasureWizardView)
    
    # cookie name keys:
    data_cookie = 'MeasureWizardView_data'          # pseudo-session data
    step_cookie = 'MeasureWizardView_last_current'  # last viewed form step
    
    default_step = IMeasureWizardNaming
    
    context_hooks = (flex_context_hook,)
    
    def __init__(self, context, request):
        self.context = context      # context here is folder/container
        self.__parent__ = context   # zope2 security permission acquisition
        self.request = request
        self.portal = getSite()
        self._secret = queryUtility(IKeyManager).secret()
    
    def formdata(self):
        encoded_data = self.request.cookies.get(self.data_cookie, None)
        if not encoded_data:
            return None
        data = SignedPickleIO(self._secret).loads(encoded_data)
        return data
    
    def set_formdata(self, data):
        msg = SignedPickleIO(self._secret).dumps(data)
        self.request.response.setCookie(self.data_cookie, msg)
    
    @property
    def step_title(self):
        return STEP_TITLES.get(self.current_step, u'')
    
    @property
    def stepmap(self):
        return dict(
            [(o.__name__, o) for o in STEP_TITLES.keys()]
            )  # name->interface
    
    def get_submitted_source_step(self):
        stepmap = self.stepmap
        current_step = self.default_step   # initially, start state
        ## hidden form input always documents the source interface name:
        source = self.request.form.get(STEP_STATE_KEY, None)
        if source in stepmap:
            return stepmap.get(source)
        return current_step  # fallback
    
    def get_current_form_step(self):
        current_step = self.default_step   # initially, start state
        stepmap = self.stepmap
        ## hidden form input always documents the source interface name:
        source = self.request.form.get(STEP_STATE_KEY, None)
        submitted_buttons = [ 
            k.replace('form.buttons.','') for k in self.request.form
            if k.startswith('form.buttons')
            ]
        if submitted_buttons and source in stepmap:
            ## execute transition to new step/state, return that state
            source_iface = stepmap.get(source)
            delta_table = delta_tables.get(source_iface)
            button_action = submitted_buttons[0]   # transition
            current_step = delta_table.get(button_action, current_step)
            if button_action not in delta_table and button_action == 'next':
                ## a branch
                if 'form.widgets.form_type' in self.request.form:
                    form_ftitype = self.request.form.get('form.widgets.form_type')[0]
                    if form_ftitype == SIMPLE_FORM_TYPE:
                        current_step = delta_table.get('branch_flex', current_step)
                    else:
                        current_step = delta_table.get('branch_mr', current_step)
        return current_step  # may be None on final state
    
    def _sorted_buttons(self, buttons):
        if 'previous' in buttons:
            # previous always first button
            return ( 
                ['previous'] +
                filter(lambda v: v != 'previous', buttons)
                )
        return list(buttons)  # original sort
    
    def buttons(self, step):
        """
        Return button.Buttons instance with all buttons to display for
        wizard step, including a 'cancel' button.
        """
        delta_table = delta_tables[step]
        transition_names = delta_table.keys()
        branches = filter(lambda v: v.startswith('branch_'), transition_names)
        buttons = list(set(transition_names) - set(branches))
        buttons = self._sorted_buttons(buttons)
        if branches:
            buttons += ['next']
        buttons += ['cancel']
        buttons = [(v, BUTTON_LABELS[v]) for v in buttons]
        return button.Buttons(
            *[button.Button(name, title) for name, title in buttons]
            )
    
    def submitted_data(self):
        data = {'all_data': self.formdata() or {}}
        data_schema = self.get_submitted_source_step()
        form = WizardStepForm(self.context, self.request, data_schema, data)
        form.update()
        data, errors = form.extractData()
        return data_schema, data, errors, form
    
    def _merged_data(self, data_schema, saved_formdata, data):
        _key = data_schema.__name__  # keyed by interface name
        merged = copy.deepcopy(saved_formdata)
        merged[_key] = data
        return merged
   
    def remove_buttons_from_request_data(self):
        """
        Remove all keys in self.request.form starting with 'form.buttons'
        """
        _buttonkey = lambda v: v.startswith('form.buttons')
        for k in filter(_buttonkey, self.request.form):
            del(self.request.form[k])
            if k in self.request.other:
                del(self.request.other[k])
    
    def source_context(self, step_schema, data):
        result = None
        for hook in self.context_hooks:
            result = hook(self.context, step_schema, data)
        return result

    def update_next_step_form(self, data=None, current=None):
        ## fallbacks for all data, current step schema, saved data for step
        data = data if data is not None else {}
        current = current if current is not None else self.current_step
        saved_current = data.get(current.__name__, {}) if data else {}
        ## special cases, pass into saved_current context for sources:
        source_context = self.source_context(current, data)
        saved_current['all_data'] = data
        if source_context:
            saved_current['source_context'] = source_context
        
        ## create form instance
        form = WizardStepForm(
            self.context,
            self.request,
            current,
            data=saved_current,
            )
        form.buttons = self.buttons(current)
        form.update()
        self.formbody = form.render()
        return form
    
    def clear_wizard_cookies(self):
        if self.data_cookie in self.request.cookies:
            self.request.response.expireCookie(self.data_cookie)
            del(self.request.cookies[self.data_cookie])
        if self.step_cookie in self.request.cookies:
            self.request.response.expireCookie(self.step_cookie)
            del(self.request.cookies[self.step_cookie])

    def widget_traversal_form(self):
        current_step = self.request.cookies.get(self.step_cookie, None)
        if current_step is not None:
            current_step = self.stepmap.get(current_step)
        else:
            current_step = self.get_current_form_step()
        self.remove_buttons_from_request_data()
        return self.update_next_step_form(current=current_step)
    
    def handle_errors(self, errors):
        messages = IStatusMessage(self.request)
        messages.add('Please fix highlighted errors', type='error')
    
    def update_final(self, *args, **kwargs):
        """
        A final state/step of the form has been reached, and
        a 'next' button has been submitted.
        """
        ## TODO: get previous state submitting to determine handler
        ## TODO: handler map step-interface to handler function
        ## TODO: factory adapters
        ## TODO: A criteria view for measure when multiple filters contained?
        ## TODO: response URL (post-creation for redirect):
        ##       (1) Flex form measure: go to measure view
        ##       (2) MR criteria view
        self.formbody = 'Your measure has been configured'  # TODO: rem
    
    def update(self, *args, **kwargs):
        method = self.request.get('REQUEST_METHOD', None)
        ## check for cancelation of wizard:
        if 'form.buttons.cancel' in self.request.form:
            self.clear_wizard_cookies()
            self.request.response.redirect(self.request.URL) # back to start
            return False  # user selected cancel, redirect
        
        ## only GET request to this view is to first step; on such, clear any
        ## cookies resulting from previous wizard sessions:
        if method != 'POST':
            self.clear_wizard_cookies()
        
        ## get form data from previous steps already submitted:
        saved_formdata = self.formdata() or {}
        
        ## process any data submitted from previous step
        if 'form.buttons.next' in self.request.form:
            data_schema, data, errors, error_form = self.submitted_data()
        
            if errors:
                self.current_step = data_schema
                error_form.buttons = self.buttons(data_schema)
                error_form.handlers = button.Handlers()
                error_form.updateActions()
                self.formbody = error_form.render()
                self.handle_errors(errors)
                return
        
            ## merge submitted data with previously saved step data, persist; 
            ## note: if there is not POSTed form data, then the merged data
            ## will not include fields for the current fieldset (usually, this
            ## just means the first page of the wizard, and that is a desired
            ## consequence -- loading the wizard again should start fresh anyway).
            saved_formdata = self._merged_data(data_schema, saved_formdata, data)
            self.set_formdata(saved_formdata)  # persist data into pickle cookie
        
        ## get current wizard step (state, used by template and methods):
        self.current_step = self.get_current_form_step()
        
        if self.current_step is None:
            return self.update_final(*args, **kwargs)
        
        ## set a cookie for last known step so that ajax/overlay requests
        ## can access ++widget++ traversal:
        self.request.response.setCookie(
            self.step_cookie,
            self.current_step.__name__,
            )
        
        ## at this point, any button values submitted in the request are
        ## going to confuse the next step form, so remove them 
        self.remove_buttons_from_request_data()
        
        ## Get form instance, add buttons, then update/render; a hidden
        ## input with current step state will be added to by WizardStepForm
        self.update_next_step_form(data=saved_formdata)
    
    def __call__(self, *args, **kwargs):
        if self.update(*args, **kwargs) != False:
            return self.index(*args, **kwargs)  # from template via Five magic


class MeasureWizardWidgetTraversal(FormWidgetTraversal):
    """
    subclass of default adapter, overrides _prepareForm method to get correct
    form context.
    """

    implements(ITraversable)
    adapts(IMeasureWizardView, IBrowserRequest)
    
    def _prepareForm(self):
        return self.context.widget_traversal_form()
