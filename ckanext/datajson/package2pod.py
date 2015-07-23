try:
    from collections import OrderedDict  # 2.7
except ImportError:
    from sqlalchemy.util import OrderedDict

from logging import getLogger

log = getLogger(__name__)

from helpers import *
from datajsonvalidator import do_validation
import ckan.model as model


class Package2Pod:
    def __init__(self):
        pass

    seen_identifiers = None

    @staticmethod
    def wrap_json_catalog(dataset_dict, json_export_map):
        catalog_headers = [(x, y) for x, y in json_export_map.get('catalog_headers').iteritems()]
        catalog = OrderedDict(
            catalog_headers + [('dataset', dataset_dict)]
        )
        return catalog

    @staticmethod
    def convert_package(package, json_export_map, validation_required=False):
        import sys, os

        try:
            dataset = Package2Pod.export_map_fields(package, json_export_map)

            # skip validation if we export whole /data.json catalog
            if validation_required:
                return Package2Pod.validate(package, dataset)
            else:
                return dataset
        except Exception as e:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            filename = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            log.error("%s : %s : %s : %s", exc_type, filename, exc_tb.tb_lineno, unicode(e))
            raise e

    @staticmethod
    def export_map_fields(package, json_export_map):
        import string
        import sys, os

        json_fields = json_export_map.get('dataset_fields_map')

        try:
            dataset = OrderedDict([("@type", "dcat:Dataset")])

            Wrappers.pkg = package
            Wrappers.full_field_map = json_fields

            for key, field_map in json_fields.iteritems():
                # log.debug('%s => %s', key, field_map)

                field_type = field_map.get('type', 'direct')
                is_extra = field_map.get('extra')
                array_key = field_map.get('array_key')
                field = field_map.get('field')
                split = field_map.get('split')
                wrapper = field_map.get('wrapper')
                default = field_map.get('default')

                if 'direct' == field_type and field:
                    if is_extra:
                        # log.debug('field: %s', field)
                        # log.debug('value: %s', get_extra(package, field))
                        dataset[key] = strip_if_string(get_extra(package, field, default))
                    else:
                        dataset[key] = strip_if_string(package.get(field, default))

                elif 'array' == field_type:
                    if is_extra:
                        found_element = strip_if_string(get_extra(package, field))
                        if found_element:
                            if is_redacted(found_element):
                                dataset[key] = found_element
                            elif split:
                                dataset[key] = [strip_if_string(x) for x in string.split(found_element, split)]

                    else:
                        if array_key:
                            dataset[key] = [strip_if_string(t[array_key]) for t in package.get(field)]
                if wrapper:
                    # log.debug('wrapper: %s', wrapper)
                    method = getattr(Wrappers, wrapper)
                    if method:
                        Wrappers.current_field_map = field_map
                        dataset[key] = method(dataset.get(key))

            # CKAN doesn't like empty values on harvest, let's get rid of them
            # Remove entries where value is None, "", or empty list []
            dataset = OrderedDict([(x, y) for x, y in dataset.iteritems() if y is not None and y != "" and y != []])

            return dataset
        except Exception as e:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            filename = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            log.error("%s : %s : %s : %s", exc_type, filename, exc_tb.tb_lineno, unicode(e))
            raise e

    @staticmethod
    def validate(pkg, dataset_dict):
        import sys, os

        try:
            # When saved from UI DataQuality value is stored as "on" instead of True.
            # Check if value is "on" and replace it with True.
            dataset_dict = OrderedDict(dataset_dict)
            if dataset_dict.get('dataQuality') == "on" \
                    or dataset_dict.get('dataQuality') == "true" \
                    or dataset_dict.get('dataQuality') == "True":
                dataset_dict['dataQuality'] = True
            elif dataset_dict.get('dataQuality') == "false" \
                    or dataset_dict.get('dataQuality') == "False":
                dataset_dict['dataQuality'] = False

            errors = []
            try:
                do_validation([dict(dataset_dict)], errors, Package2Pod.seen_identifiers)
            except Exception as e:
                errors.append(("Internal Error", ["Something bad happened: " + unicode(e)]))
            if len(errors) > 0:
                for error in errors:
                    log.warn(error)

                errors_dict = OrderedDict([
                    ('id', pkg.get('id')),
                    ('name', pkg.get('name')),
                    ('title', pkg.get('title')),
                    ('organization', currentPackageOrg),
                    ('errors', errors),
                ])

                return errors_dict

            return dataset_dict
        except Exception as e:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            filename = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            log.error("%s : %s : %s", exc_type, filename, exc_tb.tb_lineno)
            raise e


class Wrappers:
    def __init__(self):
        pass

    pkg = None
    current_field_map = None
    full_field_map = None

    @staticmethod
    def catalog_publisher(value):
        return OrderedDict([
            ("@type", "org:Organization"),
            ("name", get_responsible_party(get_extra(Wrappers.pkg, Wrappers.current_field_map.get('field'))))
        ])

    @staticmethod
    def inventory_publisher(value):
        global currentPackageOrg

        publisher = strip_if_string(get_extra(Wrappers.pkg, Wrappers.current_field_map.get('field')))
        if publisher is None:
            return None

        currentPackageOrg = publisher

        organization_list = list()
        organization_list.append([
            ('@type', 'org:Organization'),  # optional
            ('name', publisher),  # required
        ])

        for i in range(1, 6):
            pub_key = 'publisher_' + str(i)
            if get_extra(Wrappers.pkg, pub_key):
                organization_list.append([
                    ('@type', 'org:Organization'),  # optional
                    ('name', get_extra(Wrappers.pkg, pub_key)),  # required
                ])
                currentPackageOrg = get_extra(Wrappers.pkg, pub_key)

        size = len(organization_list)

        tree = False
        for i in range(0, size):
            if tree:
                organization_list[i] += [('subOrganizationOf', OrderedDict(tree))]
            tree = organization_list[i]

        return OrderedDict(tree)

    # used by get_accrual_periodicity
    accrual_periodicity_dict = {
        'completely irregular': 'irregular',
        'decennial': 'R/P10Y',
        'quadrennial': 'R/P4Y',
        'annual': 'R/P1Y',
        'bimonthly': 'R/P2M',  # or R/P0.5M
        'semiweekly': 'R/P3.5D',
        'daily': 'R/P1D',
        'biweekly': 'R/P2W',  # or R/P0.5W
        'semiannual': 'R/P6M',
        'biennial': 'R/P2Y',
        'triennial': 'R/P3Y',
        'three times a week': 'R/P0.33W',
        'three times a month': 'R/P0.33M',
        'continuously updated': 'R/PT1S',
        'monthly': 'R/P1M',
        'quarterly': 'R/P3M',
        'semimonthly': 'R/P0.5M',
        'three times a year': 'R/P4M',
        'weekly': 'R/P1W',
        'continual': 'R/PT1S',
        'fortnightly': 'R/P0.5M',
        'annually': 'R/P1Y',
        'biannualy': 'R/P0.5Y',
        'asneeded': 'irregular',
        'irregular': 'irregular',
        'notplanned': 'irregular',
        'unknown': 'irregular',
        'not updated': 'irregular'
    }

    @staticmethod
    def fix_accrual_periodicity(frequency):
        return Wrappers.accrual_periodicity_dict.get(str(frequency).lower().strip(), frequency)

    @staticmethod
    def build_contact_point(someValue):
        import sys, os

        try:
            contact_point_map = Wrappers.full_field_map.get('contactPoint').get('map')
            if not contact_point_map:
                return None

            package = Wrappers.pkg

            if contact_point_map.get('fn').get('extra'):
                fn = get_extra(package, contact_point_map.get('fn').get('field'),
                               get_extra(package, "Contact Name"))
            else:
                fn = package.get(contact_point_map.get('fn').get('field'))

            fn = get_responsible_party(fn)

            if contact_point_map.get('hasEmail').get('extra'):
                email = get_extra(package, contact_point_map.get('hasEmail').get('field'))
            else:
                email = package.get(contact_point_map.get('hasEmail').get('field'))

            if not is_redacted(email):
                if '@' not in email:
                    # raise KeyError('contact_email')
                    return None
                else:
                    email = 'mailto:' + email

            contact_point = OrderedDict([('@type', 'vcard:Contact')])
            if fn:
                contact_point['fn'] = fn
            if email:
                contact_point['hasEmail'] = email

            return contact_point
        except Exception as e:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            filename = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            log.error("%s : %s : %s", exc_type, filename, exc_tb.tb_lineno)
            raise e

    @staticmethod
    def inventory_parent_uid(parent_dataset_id):
        if parent_dataset_id:
            parent = model.Package.get(parent_dataset_id)
            parent_uid = parent.extras.col.target['unique_id'].value
            if parent_uid:
                parent_dataset_id = parent_uid
        return parent_dataset_id

    @staticmethod
    def generate_distribution(someValue):

        arr = []
        package = Wrappers.pkg

        distribution_map = Wrappers.full_field_map.get('distribution').get('map')
        if not distribution_map:
            return None

        for r in package["resources"]:
            resource = OrderedDict([('@type', "dcat:Distribution")])

            for pod_key, json_map in distribution_map.iteritems():
                value = strip_if_string(r.get(json_map.get('field'), json_map.get('default')))
                if value:
                    resource[pod_key] = value

            # inventory rules
            res_url = strip_if_string(r.get('url'))
            if res_url:
                res_url = res_url.replace('http://[[REDACTED', '[[REDACTED')
                res_url = res_url.replace('http://http', 'http')
                if r.get('resource_type') in ['api', 'accessurl']:
                    resource['accessURL'] = res_url
                else:
                    if 'accessURL' in resource:
                        resource.pop('accessURL')
                    resource['downloadURL'] = res_url
                    if 'mediaType' not in resource:
                        log.warn("Missing mediaType sdf for resource in package ['%s']", package.get('id'))
            else:
                log.warn("Missing downloadURL for resource in package ['%s']", package.get('id'))

            striped_resource = OrderedDict(
                [(x, y) for x, y in resource.iteritems() if y is not None and y != "" and y != []])

            arr += [OrderedDict(striped_resource)]

        return arr
