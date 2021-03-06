"""
Handles scanning through the zip packages to find all items, styles, etc.
"""
import os
import os.path
import shutil
from zipfile import ZipFile
from collections import defaultdict, namedtuple

from property_parser import Property, NoKeyError
from FakeZip import FakeZip, zip_names
from selectorWin import SelitemData
from loadScreen import main_loader as loader
import extract_packages
import utils


__all__ = [
    'load_packages',
    'Style',
    'Item',
    'QuotePack',
    'Skybox',
    'Music',
    'StyleVar',
    ]

all_obj = {}
obj_override = {}
packages = {}
OBJ_TYPES = {}

data = {}

res_count = -1

ObjData = namedtuple('ObjData', 'zip_file, info_block, pak_id, disp_name')
ParseData = namedtuple('ParseData', 'zip_file, id, info, pak_id')
PackageData = namedtuple('package_data', 'zip_file, info, name, disp_name')
ObjType = namedtuple('ObjType', 'cls, allow_mult, has_img')


def pak_object(name, allow_mult=False, has_img=True):
    """Decorator to add a class to the list of objects.

    Each object class needs two methods:
    parse() gets called with a ParseData object, to read from info.txt.
    The return value gets saved.

    For override items, they are parsed normally. The original item then
    gets the add_over(override) method called for each override to add values.

    If allow_mult is true, duplicate items will be treated as overrides,
    with one randomly chosen to be the 'parent'.
    """
    def x(cls):
        OBJ_TYPES[name] = ObjType(cls, allow_mult, has_img)
        return cls
    return x


def reraise_keyerror(err, obj_id):
    """Replace NoKeyErrors with a nicer one, giving the item that failed."""
    if isinstance(err, IndexError):
        if isinstance(err.__cause__, NoKeyError):
            # Property.__getitem__ raises IndexError from
            # NoKeyError, so read from the original
            key_error = err.__cause__
        else:
            # We shouldn't have caught this
            raise err
    else:
        key_error = err
    raise Exception(
        'No "{key}" in {id!s} object!'.format(
            key=key_error.key,
            id=obj_id,
        )
    ) from err


def get_config(prop_block, zip_file, folder, pak_id='', prop_name='config'):
    """Extract a config file refered to by the given property block.

    Looks for the prop_name key in the given prop_block.
    If the keyvalue has a value of "", an empty tree is returned.
    If it has children, a copy of them is returned.
    Otherwise the value is a filename in the zip which will be parsed.
    """
    prop_block = prop_block.find_key(prop_name, "")
    if prop_block.has_children():
        prop = prop_block.copy()
        prop.name = None
        return prop

    if prop_block.value == '':
        return Property(None, [])

    path = os.path.join(folder, prop_block.value) + '.cfg'
    try:
        with zip_file.open(path) as f:
            return Property.parse(f,
            pak_id + ':' + path,
            )
    except KeyError:
        print('"{}:{}" not in zip!'.format(pak_id, path))
        return Property(None, [])


def find_packages(pak_dir, zips, zip_name_lst):
    """Search a folder for packages, recursing if necessary."""
    found_pak = False
    for name in os.listdir(pak_dir):  # Both files and dirs
        name = os.path.join(pak_dir, name)
        is_dir = os.path.isdir(name)
        if name.endswith('.zip') and os.path.isfile(name):
            zip_file = ZipFile(name)
        elif is_dir:
            zip_file = FakeZip(name)
        else:
            utils.con_log('Extra file: ', name)
            continue

        if 'info.txt' in zip_file.namelist():  # Is it valid?
            zips.append(zip_file)
            zip_name_lst.append(os.path.abspath(name))
            print('Reading package "' + name + '"')
            with zip_file.open('info.txt') as info_file:
                info = Property.parse(info_file, name + ':info.txt')
            pak_id = info['ID']
            disp_name = info['Name', pak_id]
            packages[pak_id] = PackageData(
                zip_file,
                info,
                name,
                disp_name,
            )
            found_pak = True
        else:
            if is_dir:
                # This isn't a package, so check the subfolders too...
                print('Checking subdir "{}" for packages...'.format(name))
                find_packages(name, zips, zip_name_lst)
            else:
                zip_file.close()
                print('ERROR: Bad package "{}"!'.format(name))
    if not found_pak:
        print('No packages in folder!')


def load_packages(
        pak_dir,
        log_item_fallbacks=False,
        log_missing_styles=False,
        log_missing_ent_count=False,
        ):
    """Scan and read in all packages in the specified directory."""
    global LOG_ENT_COUNT
    pak_dir = os.path.abspath(os.path.join(os.getcwd(), '..', pak_dir))

    if not os.path.isdir(pak_dir):
        from tkinter import messagebox
        import sys
        # We don't have a packages directory!
        messagebox.showerror(
            master=loader,
            title='BEE2 - Invalid Packages Directory!',
            message='The given packages directory is not present!\n'
                    'Get the packages from '
                    '"http://github.com/TeamSpen210/BEE2-items" '
                    'and place them in "' + pak_dir +
                    os.path.sep + '".',
                    # Add slash to the end to indicate it's a folder.
        )
        sys.exit('No Packages Directory!')

    LOG_ENT_COUNT = log_missing_ent_count
    print('ENT_COUNT:', LOG_ENT_COUNT)
    zips = []
    data['zips'] = []
    try:
        find_packages(pak_dir, zips, data['zips'])

        loader.set_length("PAK", len(packages))

        for obj_type in OBJ_TYPES:
            all_obj[obj_type] = {}
            obj_override[obj_type] = defaultdict(list)
            data[obj_type] = []

        objects = 0
        images = 0
        for pak_id, (zip_file, info, name, dispName) in packages.items():
            print(
                ("Reading objects from '" + pak_id + "'...").ljust(50),
                end=''
            )
            obj_count, img_count = parse_package(
                zip_file,
                info,
                pak_id,
                dispName,
            )
            objects += obj_count
            images += img_count
            loader.step("PAK")
            print("Done!")

        loader.set_length("OBJ", objects)
        loader.set_length("IMG_EX", images)

        # The number of images we need to load is the number of objects,
        # excluding some types like Stylevars or PackLists.
        loader.set_length(
            "IMG",
            sum(
                len(all_obj[key])
                for key, opts in
                OBJ_TYPES.items()
                if opts.has_img
            )
        )

        for obj_type, objs in all_obj.items():
            for obj_id, obj_data in objs.items():
                print("Loading " + obj_type + ' "' + obj_id + '"!')
                # parse through the object and return the resultant class
                try:
                    object_ = OBJ_TYPES[obj_type].cls.parse(
                        ParseData(
                            obj_data.zip_file,
                            obj_id,
                            obj_data.info_block,
                            obj_data.pak_id,
                        )
                    )
                except (NoKeyError, IndexError) as e:
                    reraise_keyerror(e, obj_id)

                object_.pak_id = obj_data.pak_id
                object_.pak_name = obj_data.disp_name
                for override_data in obj_override[obj_type].get(obj_id, []):
                    override = OBJ_TYPES[obj_type].cls.parse(
                        override_data
                    )
                    object_.add_over(override)
                data[obj_type].append(object_)
                loader.step("OBJ")

        cache_folder = os.path.abspath('../cache/')

        shutil.rmtree(cache_folder, ignore_errors=True)
        img_loc = os.path.join('resources', 'bee2')
        for zip_file in zips:
            for path in zip_names(zip_file):
                loc = os.path.normcase(path).casefold()
                if loc.startswith(img_loc):
                    loader.step("IMG_EX")
                    zip_file.extract(path, path=cache_folder)

        shutil.rmtree('../images/cache', ignore_errors=True)
        if os.path.isdir("../cache/resources/bee2"):
            shutil.move("../cache/resources/bee2", "../images/cache")
        shutil.rmtree('../cache/', ignore_errors=True)

    finally:
        # close them all, we've already read the contents.
        for z in zips:
            z.close()

    print('Allocating styled items...')
    setup_style_tree(
        data['Item'],
        data['Style'],
        log_item_fallbacks,
        log_missing_styles,
    )
    print(data['zips'])
    print('Done!')
    return data


def parse_package(zip_file, info, pak_id, disp_name):
    """Parse through the given package to find all the components."""
    for pre in Property.find_key(info, 'Prerequisites', []).value:
        if pre.value not in packages:
            utils.con_log(
                'Package "' +
                pre.value +
                '" required for "' +
                pak_id +
                '" - ignoring package!'
            )
            return False
    objects = 0
    # First read through all the components we have, so we can match
    # overrides to the originals
    for comp_type in OBJ_TYPES:
        allow_dupes = OBJ_TYPES[comp_type].allow_mult
        # Look for overrides
        for obj in info.find_all("Overrides", comp_type):
            obj_id = obj['id']
            obj_override[comp_type][obj_id].append(
                ParseData(zip_file, obj_id, obj, pak_id)
            )

        for obj in info.find_all(comp_type):
            obj_id = obj['id']
            if obj_id in all_obj[comp_type]:
                if allow_dupes:
                    # Pretend this is an override
                    obj_override[comp_type][obj_id].append(
                        ParseData(zip_file, obj_id, obj, pak_id)
                    )
                else:
                    raise Exception('ERROR! "' + obj_id + '" defined twice!')
            objects += 1
            all_obj[comp_type][obj_id] = ObjData(
                zip_file,
                obj,
                pak_id,
                disp_name,
            )

    img_count = 0
    img_loc = os.path.join('resources', 'bee2')
    for item in zip_names(zip_file):
        item = os.path.normcase(item).casefold()
        if item.startswith("resources"):
            extract_packages.res_count += 1
            if item.startswith(img_loc):
                img_count += 1
    return objects, img_count


def setup_style_tree(item_data, style_data, log_fallbacks, log_missing_styles):
    """Modify all items so item inheritance is properly handled.

    This will guarantee that all items have a definition for each
    combination of item and version.
    The priority is:
    - Exact Match
    - Parent style
    - Grandparent (etc) style
    - First version's style
    - First style of first version
    """
    all_styles = {}

    for style in style_data:
        all_styles[style.id] = style

    for style in all_styles.values():
        base = []
        b_style = style
        while b_style is not None:
            # Recursively find all the base styles for this one
            base.append(b_style)
            b_style = all_styles.get(b_style.base_style, None)
            # Just append the style.base_style to the list,
            # until the style with that ID isn't found anymore.
        style.bases = base

    # All styles now have a .bases attribute, which is a list of the
    # parent styles that exist.

    # To do inheritance, we simply copy the data to ensure all items
    # have data defined for every used style.
    for item in item_data:
        all_ver = list(item.versions.values())
        # Move default version to the beginning, so it's read first
        all_ver.remove(item.def_ver)
        all_ver.insert(0, item.def_ver)
        for vers in all_ver:
            for sty_id, style in all_styles.items():
                if sty_id in vers['styles']:
                    continue  # We already have a definition
                for base_style in style.bases:
                    if base_style.id in vers['styles']:
                        # Copy the values for the parent to the child style
                        vers['styles'][sty_id] = vers['styles'][base_style.id]
                        if log_fallbacks and not item.unstyled:
                            print(
                                'Item "{item}" using parent '
                                '"{rep}" for "{style}"!'.format(
                                    item=item.id,
                                    rep=base_style.id,
                                    style=sty_id,
                                )
                            )
                        break
                else:
                    # For the base version, use the first style if
                    # a styled version is not present
                    if vers['id'] == item.def_ver['id']:
                        vers['styles'][sty_id] = vers['def_style']
                        if log_missing_styles and not item.unstyled:
                            print(
                                'Item "{item}" using '
                                'inappropriate style for "{style}"!'.format(
                                    item=item.id,
                                    style=sty_id,
                                )
                            )
                    else:
                        # For versions other than the first, use
                        # the base version's definition
                        vers['styles'][sty_id] = item.def_ver['styles'][sty_id]


def parse_item_folder(folders, zip_file, pak_id):
    for fold in folders:
        prop_path = 'items/' + fold + '/properties.txt'
        editor_path = 'items/' + fold + '/editoritems.txt'
        config_path = 'items/' + fold + '/vbsp_config.cfg'
        try:
            with zip_file.open(prop_path, 'r') as prop_file:
                props = Property.parse(
                    prop_file, pak_id + ':' + prop_path,
                ).find_key('Properties')
            with zip_file.open(editor_path, 'r') as editor_file:
                editor = Property.parse(
                    editor_file, pak_id + ':' + editor_path
                )
        except KeyError as err:
            # Opening the files failed!
            raise IOError(
                '"' + pak_id + ':items/' + fold + '" not valid!'
                'Folder likely missing! '
                ) from err

        editor_iter = Property.find_all(editor, 'Item')
        folders[fold] = {
            'auth':     sep_values(props['authors', '']),
            'tags':     sep_values(props['tags', '']),
            'desc':     list(desc_parse(props)),
            'ent':      props['ent_count', '??'],
            'url':      props['infoURL', None],
            'icons':    {p.name: p.value for p in props['icon', []]},
            'all_name': props['all_name', None],
            'all_icon': props['all_icon', None],
            'vbsp':     Property(None, []),

            # The first Item block found
            'editor': next(editor_iter),
            # Any extra blocks (offset catchers, extent items)
            'editor_extra': list(editor_iter),
        }

        if LOG_ENT_COUNT and folders[fold]['ent'] == '??':
            print('Warning: "{}:{}" has missing entity count!'.format(
                pak_id, prop_path,
            ))

        # If we have at least 1, but not all of the grouping icon
        # definitions then notify the author.
        num_group_parts = (
            (folders[fold]['all_name'] is not None)
            + (folders[fold]['all_icon'] is not None)
            + ('all' in folders[fold]['icons'])
        )
        if 0 < num_group_parts < 3:
            print(
                'Warning: "{}:{}" has incomplete grouping icon '
                'definition!'.format(
                    pak_id, prop_path
                )
            )
        try:
            with zip_file.open(config_path, 'r') as vbsp_config:
                folders[fold]['vbsp'] = Property.parse(
                    vbsp_config,
                    pak_id + ':' + config_path,
                )
        except KeyError:
            folders[fold]['vbsp'] = Property(None, [])


@pak_object('Style')
class Style:
    def __init__(
            self,
            style_id,
            selitem_data: 'SelitemData',
            editor,
            config=None,
            base_style=None,
            suggested=None,
            has_video=True,
            corridor_names=utils.EmptyMapping,
            ):
        self.id = style_id
        self.selitem_data = selitem_data
        self.editor = editor
        self.base_style = base_style
        self.bases = []  # Set by setup_style_tree()
        self.suggested = suggested or {}
        self.has_video = has_video
        self.corridor_names = {
            'sp_entry': corridor_names.get('sp_entry', Property('', [])),
            'sp_exit':  corridor_names.get('sp_exit', Property('', [])),
            'coop':     corridor_names.get('coop', Property('', [])),
        }
        if config is None:
            self.config = Property(None, [])
        else:
            self.config = config

    @classmethod
    def parse(cls, data):
        """Parse a style definition."""
        info = data.info
        selitem_data = get_selitem_data(info)
        base = info['base', '']
        has_video = utils.conv_bool(info['has_video', '1'])

        sugg = info.find_key('suggested', [])
        sugg = (
            sugg['quote', '<NONE>'],
            sugg['music', '<NONE>'],
            sugg['skybox', 'SKY_BLACK'],
            sugg['goo', 'GOO_NORM'],
            sugg['elev', '<NONE>'],
            )

        corridors = info.find_key('corridors', [])
        corridors = {
            'sp_entry': corridors.find_key('sp_entry', []),
            'sp_exit':  corridors.find_key('sp_exit', []),
            'coop':     corridors.find_key('coop', []),
        }

        if base == '':
            base = None
        folder = 'styles/' + info['folder']
        config = folder + '/vbsp_config.cfg'
        with data.zip_file.open(folder + '/items.txt', 'r') as item_data:
            items = Property.parse(
                item_data,
                data.pak_id+':'+folder+'/items.txt'
            )

        try:
            with data.zip_file.open(config, 'r') as vbsp_config:
                vbsp = Property.parse(
                    vbsp_config,
                    data.pak_id+':'+config,
                )
        except KeyError:
            vbsp = None
        return cls(
            style_id=data.id,
            selitem_data=selitem_data,
            editor=items,
            config=vbsp,
            base_style=base,
            suggested=sugg,
            has_video=has_video,
            corridor_names=corridors,
            )

    def add_over(self, override: 'Style'):
        """Add the additional commands to ourselves."""
        self.editor.extend(override.editor)
        self.config.extend(override.config)
        self.selitem_data.auth.extend(override.selitem_data.auth)

    def __repr__(self):
        return '<Style:' + self.id + '>'


@pak_object('Item')
class Item:
    def __init__(
            self,
            item_id,
            versions,
            def_version,
            needs_unlock=False,
            all_conf=None,
            unstyled=False,
            glob_desc=(),
            desc_last=False
            ):
        self.id = item_id
        self.versions = versions
        self.def_ver = def_version
        self.def_data = def_version['def_style']
        self.needs_unlock = needs_unlock
        self.all_conf = all_conf or Property(None, [])
        self.unstyled = unstyled
        self.glob_desc = glob_desc
        self.glob_desc_last = desc_last

    @classmethod
    def parse(cls, data):
        """Parse an item definition."""
        versions = {}
        def_version = None
        folders = {}
        unstyled = utils.conv_bool(data.info['unstyled', '0'])

        glob_desc = list(desc_parse(data.info))
        desc_last = utils.conv_bool(data.info['AllDescLast', '0'])

        all_config = get_config(
            data.info,
            data.zip_file,
            'items',
            pak_id=data.pak_id,
            prop_name='all_conf',
        )

        needs_unlock = utils.conv_bool(data.info['needsUnlock', '0'])

        for ver in data.info.find_all('version'):
            vals = {
                'name':    ver['name', 'Regular'],
                'id':      ver['ID', 'VER_DEFAULT'],
                'is_wip': utils.conv_bool(ver['wip', '0']),
                'is_dep':  utils.conv_bool(ver['deprecated', '0']),
                'styles':  {},
                'def_style': None,
                }
            for sty_list in ver.find_all('styles'):
                for sty in sty_list:
                    if vals['def_style'] is None:
                        vals['def_style'] = sty.value
                    vals['styles'][sty.real_name] = sty.value
                    folders[sty.value] = True
            versions[vals['id']] = vals
            if def_version is None:
                def_version = vals

        parse_item_folder(folders, data.zip_file, data.pak_id)

        for ver in versions.values():
            if ver['def_style'] in folders:
                ver['def_style'] = folders[ver['def_style']]
            for sty, fold in ver['styles'].items():
                ver['styles'][sty] = folders[fold]

        if not versions:
            raise ValueError('Item "' + data.id + '" has no versions!')

        return cls(
            data.id,
            versions=versions,
            def_version=def_version,
            needs_unlock=needs_unlock,
            all_conf=all_config,
            unstyled=unstyled,
            glob_desc=glob_desc,
            desc_last=desc_last,
        )

    def add_over(self, override):
        """Add the other item data to ourselves."""
        for ver_id, version in override.versions.items():
            if ver_id not in self.versions:
                # We don't have that version!
                self.versions[ver_id] = version
            else:
                our_ver = self.versions[ver_id]['styles']
                for sty_id, style in version['styles'].items():
                    if sty_id not in our_ver:
                        # We don't have that style!
                        our_ver[sty_id] = style
                    else:
                        # We both have a matching folder, merge the
                        # definitions
                        our_style = our_ver[sty_id]

                        our_style['auth'].extend(style['auth'])
                        our_style['desc'].extend(style['desc'])
                        our_style['tags'].extend(style['tags'])
                        our_style['vbsp'] += style['vbsp']

    def __repr__(self):
        return '<Item:' + self.id + '>'


@pak_object('QuotePack')
class QuotePack:
    def __init__(
            self,
            quote_id,
            selitem_data: 'SelitemData',
            config,
            chars=None,
            ):
        self.id = quote_id
        self.selitem_data = selitem_data
        self.config = config
        self.chars = chars or ['??']

    @classmethod
    def parse(cls, data):
        """Parse a voice line definition."""
        selitem_data = get_selitem_data(data.info)
        chars = {
            char.strip()
            for char in
            data.info['characters', ''].split(',')
            if char.strip()
        }

        config = get_config(
            data.info,
            data.zip_file,
            'voice',
            pak_id=data.pak_id,
            prop_name='file',
        )

        return cls(
            data.id,
            selitem_data,
            config,
            chars=chars,
            )

    def add_over(self, override: 'QuotePack'):
        """Add the additional lines to ourselves."""
        self.selitem_data.auth += override.selitem_data.auth
        self.config += override.config
        self.config.merge_children(
            'quotes_sp',
            'quotes_coop',
        )

    def __repr__(self):
        return '<Voice:' + self.id + '>'


@pak_object('Skybox')
class Skybox:
    def __init__(
            self,
            sky_id,
            selitem_data: 'SelitemData',
            config,
            mat,
            ):
        self.id = sky_id
        self.selitem_data = selitem_data
        self.material = mat
        self.config = config

    @classmethod
    def parse(cls, data):
        """Parse a skybox definition."""
        selitem_data = get_selitem_data(data.info)
        mat = data.info['material', 'sky_black']
        config = get_config(
            data.info,
            data.zip_file,
            'skybox',
            pak_id=data.pak_id,
        )
        return cls(
            data.id,
            selitem_data,
            config,
            mat,
        )

    def add_over(self, override: 'Skybox'):
        """Add the additional vbsp_config commands to ourselves."""
        self.selitem_data.auth.extend(override.selitem_data.auth)
        self.config.extend(override.config)

    def __repr__(self):
        return '<Skybox ' + self.id + '>'


@pak_object('Music')
class Music:
    def __init__(
            self,
            music_id,
            selitem_data: 'SelitemData',
            config=None,
            inst=None,
            sound=None,
            ):
        self.id = music_id
        self.config = config or Property(None, [])
        self.inst = inst
        self.sound = sound

        self.selitem_data = selitem_data

    @classmethod
    def parse(cls, data):
        """Parse a music definition."""
        selitem_data = get_selitem_data(data.info)
        inst = data.info['instance', None]
        sound = data.info['soundscript', None]

        config = get_config(
            data.info,
            data.zip_file,
            'skybox',
            pak_id=data.pak_id,
        )
        return cls(
            data.id,
            selitem_data,
            inst=inst,
            sound=sound,
            config=config,
            )

    def add_over(self, override: 'Music'):
        """Add the additional vbsp_config commands to ourselves."""
        self.config.extend(override.config)
        self.selitem_data.auth.extend(override.selitem_data.auth)

    def __repr__(self):
        return '<Music ' + self.id + '>'


@pak_object('StyleVar', allow_mult=True, has_img=False)
class StyleVar:
    def __init__(
            self,
            var_id,
            name,
            styles,
            unstyled=False,
            default=False,
            desc='',
            ):
        self.id = var_id
        self.name = name
        self.default = default
        self.desc = desc
        if unstyled:
            self.styles = None
        else:
            self.styles = styles

    @classmethod
    def parse(cls, data):
        name = data.info['name']
        unstyled = utils.conv_bool(data.info['unstyled', '0'])
        default = utils.conv_bool(data.info['enabled', '0'])
        styles = [
            prop.value
            for prop in
            data.info.find_all('Style')
        ]
        desc = '\n'.join(
            prop.value
            for prop in
            data.info.find_all('description')
        )
        return cls(
            data.id,
            name,
            styles,
            unstyled=unstyled,
            default=default,
            desc=desc,
        )

    def add_over(self, override):
        """Override a stylevar to add more compatible styles."""
        # Setting it to be unstyled overrides any other values!
        if self.styles is None:
            return
        elif override.styles is None:
            self.styles = None
        else:
            self.styles.extend(override.styles)
        # If they both have descriptions, add them together.
        # Don't do it if they're both identical though.
        if override.desc and override.desc not in self.desc:
            if self.desc:
                self.desc += '\n\n' + override.desc
            else:
                self.desc = override.desc

    def __repr__(self):
        return '<StyleVar ' + self.id + '>'

    def applies_to_style(self, style):
        """Check to see if this will apply for the given style.

        """
        if self.styles is None:
            return True  # Unstyled stylevar

        if style.id in self.styles:
            return True

        return any(
            base in self.styles
            for base in
            style.bases
        )


@pak_object('Elevator')
class ElevatorVid:
    """An elevator video definition.

    This is mainly defined just for Valve's items - you can't pack BIKs.
    """
    def __init__(
            self,
            elev_id,
            selitem_data: 'SelitemData',
            video,
            vert_video=None,
            ):
        self.id = elev_id

        self.selitem_data = selitem_data

        if vert_video is None:
            self.has_orient = False
            self.horiz_video = video
            self.vert_video = video
        else:
            self.has_orient = True
            self.horiz_video = video
            self.vert_video = vert_video

    @classmethod
    def parse(cls, data):
        info = data.info
        selitem_data = get_selitem_data(info)

        if 'vert_video' in info:
            video = info['horiz_video']
            vert_video = info['vert_video']
        else:
            video = info['video']
            vert_video = None

        return cls(
            data.id,
            selitem_data,
            video,
            vert_video,
        )

    def add_over(self, override):
        pass

    def __repr__(self):
        return '<ElevatorVid ' + self.id + '>'


@pak_object('PackList', allow_mult=True, has_img=False)
class PackList:
    def __init__(self, pak_id, files, mats):
        self.id = pak_id
        self.files = files
        self.trigger_mats = mats

    @classmethod
    def parse(cls, data):
        conf = data.info.find_key('Config', '')
        mats = [
            prop.value
            for prop in
            data.info.find_all('AddIfMat')
        ]
        if conf.has_children():
            # Allow having a child block to define packlists inline
            files = [
                prop.value
                for prop in conf
            ]
        else:
            path = 'pack/' + conf.value + '.cfg'
            try:
                with data.zip_file.open(path) as f:
                    # Each line is a file to pack.
                    # Skip blank lines, strip whitespace, and
                    # alow // comments.
                    files = []
                    for line in f:
                        line = utils.clean_line(line)
                        if line:
                            files.append(line)
            except KeyError as ex:
                raise FileNotFoundError(
                    '"{}:{}" not in zip!'.format(
                        data.id,
                        path,
                    )
                ) from ex

        return cls(
            data.id,
            files,
            mats,
        )

    def add_over(self, override):
        """Override items just append to the list of files."""
        # Dont copy over if it's already present
        for item in override.files:
            if item not in self.files:
                self.file.append(item)

        for item in override.trigger_mats:
            if item not in self.trigger_mats:
                self.trigger_mats.append(item)


@pak_object('EditorSound')
class EditorSound:
    """Add sounds that are usable in the editor.

    The editor only reads in game_sounds_editor, so custom sounds must be
    added here.
    The ID is the name of the sound, prefixed with 'BEE2_Editor.'.
    The values in 'keys' will form the soundscript body.
    """
    def __init__(self, snd_name, data):
        self.id = 'BEE2_Editor.' + snd_name
        self.data = data
        data.name = self.id

    @classmethod
    def parse(cls, data):
        return cls(
            snd_name=data.id,
            data=data.info.find_key('keys', [])
        )


def desc_parse(info):
    """Parse the description blocks, to create data which matches richTextBox.

    """
    for prop in info.find_all("description"):
        if prop.has_children():
            for line in prop:
                yield (line.name, line.value)
        else:
            yield ("line", prop.value)


def get_selitem_data(info):
    """Return the common data for all item types - name, author, description.

    """
    auth = sep_values(info['authors', ''])
    desc = list(desc_parse(info))
    short_name = info['shortName', None]
    name = info['name']
    icon = info['icon', '_blank']
    group = info['group', '']
    if not group:
        group = None
    if not short_name:
        short_name = name

    return SelitemData(
        name,
        short_name,
        auth,
        icon,
        desc,
        group,
    )


def sep_values(string, delimiters=',;/'):
    """Split a string by a delimiter, and then strip whitespace.

    Multiple delimiter characters can be passed.
    """
    delim, *extra_del = delimiters
    if string == '':
        return []

    for extra in extra_del:
        string = string.replace(extra, delim)

    vals = string.split(delim)
    return [
        stripped for stripped in
        (val.strip() for val in vals)
        if stripped
    ]

if __name__ == '__main__':
    load_packages('packages//', False)