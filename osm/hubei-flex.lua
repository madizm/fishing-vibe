-- Selective osm2pgsql flex import for fishing-spot spatial validation.
-- Imports the full Geofabrik Hubei extract into an isolated schema.

local schema = os.getenv('OSM_SCHEMA') or 'osm_next'
local options = { schema = schema }

local admin_boundaries = osm2pgsql.define_area_table('admin_boundaries', {
    { column = 'osm_type', type = 'text', not_null = true },
    { column = 'osm_id', type = 'int8', not_null = true },
    { column = 'name', type = 'text' },
    { column = 'name_zh', type = 'text' },
    { column = 'alt_name', type = 'text' },
    { column = 'official_name', type = 'text' },
    { column = 'admin_level', type = 'int' },
    { column = 'tags', type = 'jsonb', not_null = true },
    { column = 'geom', type = 'geometry', projection = 4326, not_null = true },
}, options)

local water_bodies = osm2pgsql.define_area_table('water_bodies', {
    { column = 'osm_type', type = 'text', not_null = true },
    { column = 'osm_id', type = 'int8', not_null = true },
    { column = 'name', type = 'text' },
    { column = 'name_zh', type = 'text' },
    { column = 'alt_name', type = 'text' },
    { column = 'water_class', type = 'text', not_null = true },
    { column = 'intermittent', type = 'text' },
    { column = 'tags', type = 'jsonb', not_null = true },
    { column = 'geom', type = 'geometry', projection = 4326, not_null = true },
}, options)

local waterways = osm2pgsql.define_way_table('waterways', {
    { column = 'osm_type', type = 'text', not_null = true },
    { column = 'osm_id', type = 'int8', not_null = true },
    { column = 'name', type = 'text' },
    { column = 'name_zh', type = 'text' },
    { column = 'alt_name', type = 'text' },
    { column = 'waterway_class', type = 'text', not_null = true },
    { column = 'intermittent', type = 'text' },
    { column = 'tunnel', type = 'text' },
    { column = 'tags', type = 'jsonb', not_null = true },
    { column = 'geom', type = 'linestring', projection = 4326, not_null = true },
}, options)

local water_features = osm2pgsql.define_node_table('water_features', {
    { column = 'osm_type', type = 'text', not_null = true },
    { column = 'osm_id', type = 'int8', not_null = true },
    { column = 'name', type = 'text' },
    { column = 'name_zh', type = 'text' },
    { column = 'feature_class', type = 'text', not_null = true },
    { column = 'tags', type = 'jsonb', not_null = true },
    { column = 'geom', type = 'point', projection = 4326, not_null = true },
}, options)

local function names_and_identity(object)
    return {
        osm_type = object.type,
        osm_id = object.id,
        name = object.tags.name,
        name_zh = object.tags['name:zh'],
        alt_name = object.tags.alt_name,
    }
end

local function is_water_body(tags)
    return tags.natural == 'water'
        or tags.natural == 'wetland'
        or tags.water ~= nil
        or tags.landuse == 'reservoir'
        or tags.landuse == 'basin'
        or tags.waterway == 'riverbank'
end

local function water_class(tags)
    if tags.water then return tags.water end
    if tags.natural == 'wetland' then return 'wetland' end
    if tags.landuse == 'reservoir' then return 'reservoir' end
    if tags.landuse == 'basin' then return 'basin' end
    if tags.waterway == 'riverbank' then return 'riverbank' end
    return 'water'
end

local point_water_features = {
    dam = true,
    weir = true,
    lock_gate = true,
    sluice_gate = true,
    waterfall = true,
    water_point = true,
}

local function insert_admin(object, geom)
    local row = names_and_identity(object)
    row.official_name = object.tags.official_name
    row.admin_level = tonumber(object.tags.admin_level)
    row.tags = object.tags
    row.geom = geom
    admin_boundaries:insert(row)
end

local function insert_water_body(object, geom)
    local row = names_and_identity(object)
    row.water_class = water_class(object.tags)
    row.intermittent = object.tags.intermittent
    row.tags = object.tags
    row.geom = geom
    water_bodies:insert(row)
end

function osm2pgsql.process_node(object)
    local class = object.tags.waterway
    if class and point_water_features[class] then
        local row = names_and_identity(object)
        row.feature_class = class
        row.tags = object.tags
        row.geom = object:as_point()
        water_features:insert(row)
    end
end

function osm2pgsql.process_way(object)
    if object.is_closed and object.tags.boundary == 'administrative' then
        insert_admin(object, object:as_polygon())
    end

    if object.is_closed and is_water_body(object.tags) then
        insert_water_body(object, object:as_polygon())
    end

    local class = object.tags.waterway
    if class and class ~= 'riverbank' then
        local row = names_and_identity(object)
        row.waterway_class = class
        row.intermittent = object.tags.intermittent
        row.tunnel = object.tags.tunnel
        row.tags = object.tags
        row.geom = object:as_linestring()
        waterways:insert(row)
    end
end

function osm2pgsql.process_relation(object)
    local relation_type = object.tags.type
    if relation_type ~= 'multipolygon' and relation_type ~= 'boundary' then
        return
    end

    if object.tags.boundary == 'administrative' then
        insert_admin(object, object:as_multipolygon())
    end

    if is_water_body(object.tags) then
        insert_water_body(object, object:as_multipolygon())
    end
end
