"""Hand-written views over the raw DB2 tables.

The raw tables are faithful but unfriendly: ``ItemSparse`` alone has 103
columns of integer codes spread across four other tables.  Each view here
answers a question a person would actually ask ("what items are there?",
"what does this spell do?") using plain column names.

Every view declares the tables and columns it needs.  DB2 schemas change with
each patch, so a view whose columns have been renamed or removed is skipped
with a note in the build report instead of failing the build.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass(frozen=True)
class View:
    name: str
    description: str
    sql: str
    requires: dict[str, tuple[str, ...]] = field(default_factory=dict)


def enum(table: str, column: str, expression: str) -> str:
    """SQL that turns an integer code into its label, via the metadata tables."""
    return (
        f"(SELECT e.label FROM db2_enum e WHERE e.table_name = '{table}' "
        f"AND e.column_name = '{column}' AND e.value = {expression})"
    )


def flags(table: str, column: str, expression: str) -> str:
    """SQL that spells out a bitmask column as a comma-separated list."""
    return (
        f"(SELECT group_concat(f.label, ', ') FROM db2_flag f "
        f"WHERE f.table_name = '{table}' AND f.column_name = '{column}' "
        f"AND f.value != 0 AND ({expression} & f.value) = f.value)"
    )


VIEWS: list[View] = [
    View(
        "items",
        "Every item: name, quality, item level, type, and what it binds as.",
        f"""
        SELECT
            s.ID                                              AS item_id,
            s.Display_lang                                    AS name,
            {enum('ItemSparse', 'OverallQualityID', 's.OverallQualityID')} AS quality,
            s.ItemLevel                                       AS item_level,
            s.RequiredLevel                                   AS required_level,
            c.ClassName_lang                                  AS class,
            sc.DisplayName_lang                               AS subclass,
            {enum('ItemSparse', 'InventoryType', 's.InventoryType')}       AS slot,
            {enum('ItemSparse', 'Bonding', 's.Bonding')}                   AS bind,
            {enum('ItemSparse', 'ExpansionID', 's.ExpansionID')}           AS expansion,
            s.Description_lang                                AS description,
            s.Stackable                                       AS stack_size,
            s.MaxCount                                        AS max_count,
            s.SellPrice                                       AS sell_price,
            s.BuyPrice                                        AS buy_price,
            s.ItemSet                                         AS item_set_id,
            s.StartQuestID                                    AS starts_quest_id,
            s.RequiredSkill                                   AS required_skill_id,
            i.IconFileDataID                                  AS icon_file_id,
            s.OverallQualityID                                AS quality_id,
            i.ClassID                                         AS class_id,
            i.SubclassID                                      AS subclass_id
        FROM ItemSparse s
        LEFT JOIN Item i ON i.ID = s.ID
        LEFT JOIN ItemClass c ON c.ClassID = i.ClassID
        LEFT JOIN ItemSubClass sc ON sc.ClassID = i.ClassID AND sc.SubClassID = i.SubclassID
        """,
        {
            "ItemSparse": ("ID", "Display_lang", "OverallQualityID", "ItemLevel",
                           "RequiredLevel", "InventoryType", "Bonding", "ExpansionID"),
            "Item": ("ID", "ClassID", "SubclassID"),
            "ItemClass": ("ClassID", "ClassName_lang"),
            "ItemSubClass": ("ClassID", "SubClassID", "DisplayName_lang"),
        },
    ),
    View(
        "item_spells",
        "Spells an item casts or teaches, with trigger and cooldown.",
        """
        SELECT
            x.ItemID           AS item_id,
            s.Display_lang     AS item_name,
            e.SpellID          AS spell_id,
            n.Name_lang        AS spell_name,
            e.TriggerType      AS trigger_type,
            e.Charges          AS charges,
            e.CoolDownMSec     AS cooldown_ms
        FROM ItemXItemEffect x
        JOIN ItemEffect e ON e.ID = x.ItemEffectID
        LEFT JOIN ItemSparse s ON s.ID = x.ItemID
        LEFT JOIN SpellName n ON n.ID = e.SpellID
        """,
        {
            "ItemXItemEffect": ("ItemID", "ItemEffectID"),
            "ItemEffect": ("ID", "SpellID", "TriggerType", "Charges", "CoolDownMSec"),
            "SpellName": ("ID", "Name_lang"),
        },
    ),
    View(
        "spells",
        "Every spell: name, description, range, cast time, cooldown, level.",
        """
        SELECT
            n.ID                    AS spell_id,
            n.Name_lang             AS name,
            sp.NameSubtext_lang     AS rank,
            sp.Description_lang     AS description,
            sp.AuraDescription_lang AS aura_description,
            r.DisplayName_lang      AS range_name,
            r.RangeMax_0            AS range_max,
            ct.Base                 AS cast_time_ms,
            cd.RecoveryTime         AS cooldown_ms,
            cd.CategoryRecoveryTime AS category_cooldown_ms,
            d.Duration              AS duration_ms,
            lv.SpellLevel           AS spell_level,
            lv.MaxLevel             AS max_level,
            m.SpellIconFileDataID   AS icon_file_id,
            m.SchoolMask            AS school_mask
        FROM SpellName n
        LEFT JOIN Spell sp ON sp.ID = n.ID
        LEFT JOIN SpellMisc m ON m.SpellID = n.ID AND m.DifficultyID = 0
        LEFT JOIN SpellRange r ON r.ID = m.RangeIndex
        LEFT JOIN SpellCastTimes ct ON ct.ID = m.CastingTimeIndex
        LEFT JOIN SpellDuration d ON d.ID = m.DurationIndex
        LEFT JOIN SpellCooldowns cd ON cd.SpellID = n.ID AND cd.DifficultyID = 0
        LEFT JOIN SpellLevels lv ON lv.SpellID = n.ID AND lv.DifficultyID = 0
        """,
        {
            "SpellName": ("ID", "Name_lang"),
            "Spell": ("ID", "Description_lang", "AuraDescription_lang"),
            "SpellMisc": ("SpellID", "DifficultyID", "RangeIndex", "CastingTimeIndex",
                          "DurationIndex", "SchoolMask", "SpellIconFileDataID"),
            "SpellRange": ("ID", "DisplayName_lang", "RangeMax_0"),
            "SpellCastTimes": ("ID", "Base"),
            "SpellDuration": ("ID", "Duration"),
            "SpellCooldowns": ("SpellID", "DifficultyID", "RecoveryTime",
                               "CategoryRecoveryTime"),
            "SpellLevels": ("SpellID", "DifficultyID", "SpellLevel", "MaxLevel"),
        },
    ),
    View(
        "spell_effects",
        "What each spell actually does, one row per effect slot.",
        f"""
        SELECT
            e.SpellID                                        AS spell_id,
            n.Name_lang                                      AS spell_name,
            e.EffectIndex                                    AS effect_index,
            {enum('SpellEffect', 'Effect', 'e.Effect')}      AS effect,
            {enum('SpellEffect', 'EffectAura', 'e.EffectAura')} AS aura,
            e.EffectBasePointsF                              AS base_points,
            e.EffectAuraPeriod                               AS period_ms,
            e.EffectRadiusIndex_0                            AS radius_index,
            e.EffectTriggerSpell                             AS triggers_spell_id,
            e.EffectItemType                                 AS item_type,
            e.EffectChainTargets                             AS chain_targets,
            e.DifficultyID                                   AS difficulty_id
        FROM SpellEffect e
        LEFT JOIN SpellName n ON n.ID = e.SpellID
        """,
        {
            "SpellEffect": ("SpellID", "EffectIndex", "Effect", "EffectAura",
                            "EffectAuraPeriod", "EffectTriggerSpell", "EffectItemType",
                            "EffectChainTargets", "DifficultyID", "EffectBasePointsF",
                            "EffectRadiusIndex_0"),
            "SpellName": ("ID", "Name_lang"),
        },
    ),
    View(
        "creatures",
        "NPCs and monsters with their type, family and title.",
        """
        SELECT
            c.ID              AS creature_id,
            c.Name_lang       AS name,
            c.Title_lang      AS title,
            t.Name_lang       AS type,
            f.Name_lang       AS family,
            c.Classification  AS classification,
            c.DisplayID_0     AS display_id
        FROM Creature c
        LEFT JOIN CreatureType t ON t.ID = c.CreatureType
        LEFT JOIN CreatureFamily f ON f.ID = c.CreatureFamily
        """,
        {
            "Creature": ("ID", "Name_lang", "Title_lang", "CreatureType",
                         "CreatureFamily", "Classification", "DisplayID_0"),
            "CreatureType": ("ID", "Name_lang"),
            "CreatureFamily": ("ID", "Name_lang"),
        },
    ),
    View(
        "achievements",
        "Achievements with category, points and reward.",
        """
        SELECT
            a.ID               AS achievement_id,
            a.Title_lang       AS name,
            a.Description_lang AS description,
            a.Reward_lang      AS reward,
            cat.Name_lang      AS category,
            a.Points           AS points,
            a.Faction          AS faction,
            a.RewardItemID     AS reward_item_id,
            a.IconFileID       AS icon_file_id,
            a.Supercedes       AS supercedes_id
        FROM Achievement a
        LEFT JOIN Achievement_Category cat ON cat.ID = a.Category
        """,
        {
            "Achievement": ("ID", "Title_lang", "Description_lang", "Reward_lang",
                            "Category", "Points", "Faction", "RewardItemID"),
            "Achievement_Category": ("ID", "Name_lang"),
        },
    ),
    View(
        "zones",
        "Areas and zones, with their parent zone and continent.",
        """
        SELECT
            a.ID              AS area_id,
            a.AreaName_lang   AS name,
            parent.AreaName_lang AS parent_zone,
            m.MapName_lang    AS continent,
            a.ContinentID     AS map_id,
            a.ParentAreaID    AS parent_area_id,
            a.WildBattlePetLevelMin AS pet_level_min,
            a.WildBattlePetLevelMax AS pet_level_max
        FROM AreaTable a
        LEFT JOIN AreaTable parent ON parent.ID = a.ParentAreaID
        LEFT JOIN Map m ON m.ID = a.ContinentID
        """,
        {
            "AreaTable": ("ID", "AreaName_lang", "ParentAreaID", "ContinentID"),
            "Map": ("ID", "MapName_lang"),
        },
    ),
    View(
        "maps",
        "Every map: continents, dungeons, raids and battlegrounds.",
        f"""
        SELECT
            m.ID                                        AS map_id,
            m.MapName_lang                              AS name,
            m.Directory                                 AS directory,
            {enum('Map', 'InstanceType', 'm.InstanceType')} AS instance_type,
            {enum('Map', 'ExpansionID', 'm.ExpansionID')}   AS expansion,
            m.MapDescription0_lang                      AS description_normal,
            m.MapDescription1_lang                      AS description_heroic,
            m.MaxPlayers                                AS max_players,
            m.ParentMapID                               AS parent_map_id,
            m.AreaTableID                               AS area_id
        FROM Map m
        """,
        {"Map": ("ID", "MapName_lang", "Directory", "InstanceType", "ExpansionID",
                 "MaxPlayers", "ParentMapID", "AreaTableID")},
    ),
    View(
        "dungeons",
        "Dungeon and raid journal entries with their encounter counts.",
        """
        SELECT
            j.ID              AS journal_instance_id,
            j.Name_lang       AS name,
            j.Description_lang AS description,
            m.MapName_lang    AS map,
            m.ID              AS map_id,
            m.ExpansionID     AS expansion_id,
            (SELECT count(*) FROM JournalEncounter e WHERE e.JournalInstanceID = j.ID)
                              AS encounter_count
        FROM JournalInstance j
        LEFT JOIN Map m ON m.ID = j.MapID
        """,
        {
            "JournalInstance": ("ID", "Name_lang", "Description_lang", "MapID"),
            "JournalEncounter": ("JournalInstanceID",),
            "Map": ("ID", "MapName_lang", "ExpansionID"),
        },
    ),
    View(
        "encounters",
        "Boss encounters, in journal order, with the instance they belong to.",
        """
        SELECT
            e.ID               AS encounter_id,
            e.Name_lang        AS name,
            e.Description_lang AS description,
            j.Name_lang        AS instance,
            e.JournalInstanceID AS journal_instance_id,
            e.OrderIndex       AS order_index,
            e.DungeonEncounterID AS dungeon_encounter_id,
            e.UiMapID          AS ui_map_id
        FROM JournalEncounter e
        LEFT JOIN JournalInstance j ON j.ID = e.JournalInstanceID
        """,
        {
            "JournalEncounter": ("ID", "Name_lang", "Description_lang",
                                 "JournalInstanceID", "OrderIndex"),
            "JournalInstance": ("ID", "Name_lang"),
        },
    ),
    View(
        "mounts",
        "Collectable mounts and how they are obtained.",
        """
        SELECT
            m.ID              AS mount_id,
            m.Name_lang       AS name,
            m.Description_lang AS description,
            m.SourceText_lang AS source,
            m.SourceSpellID   AS spell_id,
            m.MountTypeID     AS mount_type_id,
            m.SourceTypeEnum  AS source_type
        FROM Mount m
        """,
        {"Mount": ("ID", "Name_lang", "Description_lang", "SourceText_lang",
                   "SourceSpellID", "MountTypeID", "SourceTypeEnum")},
    ),
    View(
        "battle_pets",
        "Battle pet species with their pet type and source.",
        f"""
        SELECT
            b.ID                                                  AS species_id,
            c.Name_lang                                           AS name,
            b.Description_lang                                    AS description,
            b.SourceText_lang                                     AS source,
            {enum('BattlePetSpecies', 'PetTypeEnum', 'b.PetTypeEnum')} AS pet_type,
            {enum('BattlePetSpecies', 'SourceTypeEnum', 'b.SourceTypeEnum')} AS source_type,
            b.CreatureID                                          AS creature_id,
            b.SummonSpellID                                       AS summon_spell_id
        FROM BattlePetSpecies b
        LEFT JOIN Creature c ON c.ID = b.CreatureID
        """,
        {
            "BattlePetSpecies": ("ID", "Description_lang", "SourceText_lang",
                                 "PetTypeEnum", "SourceTypeEnum", "CreatureID",
                                 "SummonSpellID"),
            "Creature": ("ID", "Name_lang"),
        },
    ),
    View(
        "toys",
        "Toy box entries with the item they come from.",
        """
        SELECT
            t.ID              AS toy_id,
            s.Display_lang    AS name,
            s.Description_lang AS description,
            t.SourceText_lang AS source,
            t.ItemID          AS item_id,
            t.SourceTypeEnum  AS source_type
        FROM Toy t
        LEFT JOIN ItemSparse s ON s.ID = t.ItemID
        """,
        {
            "Toy": ("ID", "ItemID", "SourceText_lang", "SourceTypeEnum"),
            "ItemSparse": ("ID", "Display_lang", "Description_lang"),
        },
    ),
    View(
        "heirlooms",
        "Heirloom items and their upgrade chain.",
        """
        SELECT
            h.ID              AS heirloom_id,
            s.Display_lang    AS name,
            h.SourceText_lang AS source,
            h.ItemID          AS item_id,
            h.SourceTypeEnum  AS source_type
        FROM Heirloom h
        LEFT JOIN ItemSparse s ON s.ID = h.ItemID
        """,
        {
            "Heirloom": ("ID", "ItemID", "SourceText_lang", "SourceTypeEnum"),
            "ItemSparse": ("ID", "Display_lang"),
        },
    ),
    View(
        "currencies",
        "Currencies with their caps and quality.",
        """
        SELECT
            c.ID               AS currency_id,
            c.Name_lang        AS name,
            c.Description_lang AS description,
            c.CategoryID       AS category_id,
            c.Quality          AS quality,
            c.MaxQty           AS max_quantity,
            c.MaxEarnablePerWeek AS max_per_week,
            c.InventoryIconFileID AS icon_file_id
        FROM CurrencyTypes c
        """,
        {"CurrencyTypes": ("ID", "Name_lang", "Description_lang", "CategoryID",
                           "Quality", "MaxQty", "MaxEarnablePerWeek")},
    ),
    View(
        "factions",
        "Reputation factions and their parent groups.",
        """
        SELECT
            f.ID               AS faction_id,
            f.Name_lang        AS name,
            f.Description_lang AS description,
            parent.Name_lang   AS parent_faction,
            f.ParentFactionID  AS parent_faction_id,
            f.Expansion        AS expansion_id,
            f.ReputationIndex  AS reputation_index,
            f.ParagonFactionID AS paragon_faction_id,
            f.RenownFactionID  AS renown_faction_id
        FROM Faction f
        LEFT JOIN Faction parent ON parent.ID = f.ParentFactionID
        """,
        {"Faction": ("ID", "Name_lang", "Description_lang", "ParentFactionID",
                     "Expansion", "ReputationIndex")},
    ),
    View(
        "classes",
        "Playable classes.",
        """
        SELECT
            ID                AS class_id,
            Name_lang         AS name,
            Filename          AS file_name,
            Description_lang  AS description,
            RoleInfoString_lang AS role_info,
            StartingLevel     AS starting_level,
            IconFileDataID    AS icon_file_id
        FROM ChrClasses
        """,
        {"ChrClasses": ("ID", "Name_lang", "Filename", "Description_lang",
                        "StartingLevel")},
    ),
    View(
        "races",
        "Playable races with their lore blurb.",
        """
        SELECT
            ID                   AS race_id,
            Name_lang            AS name,
            Name_female_lang     AS name_female,
            LoreDescription_lang AS lore,
            FactionID            AS faction_id,
            ClientFileString     AS client_name
        FROM ChrRaces
        """,
        {"ChrRaces": ("ID", "Name_lang", "LoreDescription_lang", "FactionID",
                      "ClientFileString")},
    ),
    View(
        "specializations",
        "Class specialisations and their role.",
        f"""
        SELECT
            s.ID                                          AS spec_id,
            s.Name_lang                                   AS name,
            c.Name_lang                                   AS class,
            s.ClassID                                     AS class_id,
            {enum('ChrSpecialization', 'Role', 's.Role')} AS role,
            s.Description_lang                            AS description,
            s.OrderIndex                                  AS order_index,
            s.SpellIconFileID                             AS icon_file_id
        FROM ChrSpecialization s
        LEFT JOIN ChrClasses c ON c.ID = s.ClassID
        """,
        {
            "ChrSpecialization": ("ID", "Name_lang", "ClassID", "Role",
                                  "Description_lang", "OrderIndex"),
            "ChrClasses": ("ID", "Name_lang"),
        },
    ),
    View(
        "professions",
        "Skill lines: professions, weapon skills, class skills.",
        """
        SELECT
            ID                 AS skill_line_id,
            DisplayName_lang   AS name,
            Description_lang   AS description,
            CategoryID         AS category_id,
            ParentSkillLineID  AS parent_skill_line_id,
            SpellIconFileID    AS icon_file_id
        FROM SkillLine
        """,
        {"SkillLine": ("ID", "DisplayName_lang", "Description_lang", "CategoryID",
                       "ParentSkillLineID")},
    ),
    View(
        "recipes",
        "Spells taught by a skill line - crafting recipes and trained abilities.",
        """
        SELECT
            a.ID              AS ability_id,
            sl.DisplayName_lang AS skill_line,
            a.SkillLine       AS skill_line_id,
            n.Name_lang       AS spell_name,
            a.Spell           AS spell_id,
            a.MinSkillLineRank AS min_skill,
            a.TrivialSkillLineRankLow  AS grey_at,
            a.TrivialSkillLineRankHigh AS yellow_at,
            a.AcquireMethod   AS acquire_method,
            a.SupercedesSpell AS supercedes_spell_id
        FROM SkillLineAbility a
        LEFT JOIN SkillLine sl ON sl.ID = a.SkillLine
        LEFT JOIN SpellName n ON n.ID = a.Spell
        """,
        {
            "SkillLineAbility": ("ID", "SkillLine", "Spell", "MinSkillLineRank",
                                 "AcquireMethod", "SupercedesSpell"),
            "SkillLine": ("ID", "DisplayName_lang"),
            "SpellName": ("ID", "Name_lang"),
        },
    ),
    View(
        "talents",
        "Talent tree nodes with the spell each one grants.",
        """
        SELECT
            d.ID                     AS definition_id,
            coalesce(d.OverrideName_lang, n.Name_lang) AS name,
            coalesce(d.OverrideDescription_lang, s.Description_lang) AS description,
            d.SpellID                AS spell_id,
            d.VisibleSpellID         AS visible_spell_id,
            d.OverridesSpellID       AS overrides_spell_id
        FROM TraitDefinition d
        LEFT JOIN SpellName n ON n.ID = d.SpellID
        LEFT JOIN Spell s ON s.ID = d.SpellID
        """,
        {
            "TraitDefinition": ("ID", "SpellID", "OverrideName_lang",
                                "OverrideDescription_lang", "VisibleSpellID"),
            "SpellName": ("ID", "Name_lang"),
            "Spell": ("ID", "Description_lang"),
        },
    ),
    View(
        "pvp_talents",
        "PvP talents per specialisation.",
        """
        SELECT
            p.ID               AS pvp_talent_id,
            n.Name_lang        AS name,
            p.Description_lang AS description,
            p.SpecID           AS spec_id,
            sp.Name_lang       AS spec_name,
            p.SpellID          AS spell_id,
            p.LevelRequired    AS level_required
        FROM PvpTalent p
        LEFT JOIN SpellName n ON n.ID = p.SpellID
        LEFT JOIN ChrSpecialization sp ON sp.ID = p.SpecID
        """,
        {
            "PvpTalent": ("ID", "Description_lang", "SpecID", "SpellID",
                          "LevelRequired"),
            "SpellName": ("ID", "Name_lang"),
            "ChrSpecialization": ("ID", "Name_lang"),
        },
    ),
    View(
        "quest_lines",
        "Quest lines and the quests they contain, in order.",
        """
        SELECT
            q.ID              AS quest_line_id,
            q.Name_lang       AS name,
            q.Description_lang AS description,
            x.QuestID         AS quest_id,
            x.OrderIndex      AS order_index
        FROM QuestLine q
        LEFT JOIN QuestLineXQuest x ON x.QuestLineID = q.ID
        """,
        {
            "QuestLine": ("ID", "Name_lang", "Description_lang"),
            "QuestLineXQuest": ("QuestLineID", "QuestID", "OrderIndex"),
        },
    ),
    View(
        "quest_objectives",
        "Quest objectives - the client-side text for what a quest asks for.",
        """
        SELECT
            o.QuestID          AS quest_id,
            o.ID               AS objective_id,
            o.Description_lang AS description,
            o.Type             AS type,
            o.Amount           AS amount,
            o.ObjectID         AS object_id,
            o.OrderIndex       AS order_index
        FROM QuestObjective o
        """,
        {"QuestObjective": ("ID", "QuestID", "Description_lang", "Type", "Amount",
                            "ObjectID", "OrderIndex")},
    ),
    View(
        "item_sets",
        "Item sets and their member items.",
        """
        SELECT
            s.ID        AS item_set_id,
            s.Name_lang AS name,
            s.RequiredSkill AS required_skill_id,
            s.RequiredSkillRank AS required_skill_rank
        FROM ItemSet s
        """,
        {"ItemSet": ("ID", "Name_lang", "RequiredSkill", "RequiredSkillRank")},
    ),
    View(
        "transmog_sets",
        "Transmog appearance sets.",
        """
        SELECT
            ID              AS transmog_set_id,
            Name_lang       AS name,
            ClassMask       AS class_mask,
            ExpansionID     AS expansion_id,
            PatchIntroduced AS patch_introduced,
            TransmogSetGroupID AS group_id
        FROM TransmogSet
        """,
        {"TransmogSet": ("ID", "Name_lang", "ClassMask", "ExpansionID")},
    ),
    View(
        "titles",
        "Character titles.",
        """
        SELECT
            ID         AS title_id,
            Name_lang  AS name,
            Name1_lang AS name_female,
            Mask_ID    AS mask_id
        FROM CharTitles
        """,
        {"CharTitles": ("ID", "Name_lang", "Name1_lang", "Mask_ID")},
    ),
    View(
        "battlegrounds",
        "Battlegrounds and arenas.",
        """
        SELECT
            ID                    AS battleground_id,
            Name_lang             AS name,
            GameType_lang         AS game_type,
            ShortDescription_lang AS short_description,
            LongDescription_lang  AS long_description,
            MinLevel              AS min_level,
            MaxLevel              AS max_level,
            MaxPlayers            AS max_players,
            RatedPlayers          AS rated_players
        FROM BattlemasterList
        """,
        {"BattlemasterList": ("ID", "Name_lang", "GameType_lang", "MinLevel",
                              "MaxLevel", "MaxPlayers")},
    ),
    View(
        "lfg_dungeons",
        "Dungeon Finder entries with level ranges and difficulty.",
        """
        SELECT
            l.ID               AS lfg_dungeon_id,
            l.Name_lang        AS name,
            l.Description_lang AS description,
            m.MapName_lang     AS map,
            l.MapID            AS map_id,
            d.Name_lang        AS difficulty,
            l.DifficultyID     AS difficulty_id,
            l.ExpansionLevel   AS expansion_id,
            l.MinGear          AS min_gear
        FROM LFGDungeons l
        LEFT JOIN Map m ON m.ID = l.MapID
        LEFT JOIN Difficulty d ON d.ID = l.DifficultyID
        """,
        {
            "LFGDungeons": ("ID", "Name_lang", "Description_lang", "MapID",
                            "DifficultyID", "ExpansionLevel"),
            "Map": ("ID", "MapName_lang"),
            "Difficulty": ("ID", "Name_lang"),
        },
    ),
    View(
        "emote_commands",
        "Slash-command emotes.",
        """
        SELECT
            ID               AS emote_id,
            EmoteSlashCommand AS command,
            AnimID           AS animation_id,
            EmoteFlags       AS flags
        FROM Emotes
        """,
        {"Emotes": ("ID", "EmoteSlashCommand", "AnimID")},
    ),
    View(
        "ui_maps",
        "The world map hierarchy shown in game.",
        """
        SELECT
            u.ID            AS ui_map_id,
            u.Name_lang     AS name,
            parent.Name_lang AS parent_map,
            u.ParentUiMapID AS parent_ui_map_id,
            u.Type          AS type,
            u.System        AS system
        FROM UiMap u
        LEFT JOIN UiMap parent ON parent.ID = u.ParentUiMapID
        """,
        {"UiMap": ("ID", "Name_lang", "ParentUiMapID", "Type", "System")},
    ),
]


def create_curated_views(
    connection: sqlite3.Connection,
    schemas: dict[str, dict[str, str]],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Create every view whose dependencies are present in this build."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS wowdb_view "
        "(view_name TEXT PRIMARY KEY, description TEXT, status TEXT, note TEXT)"
    )

    created: list[str] = []
    skipped: list[tuple[str, str]] = []

    # SQLite identifiers are case-insensitive, so a curated view may not share
    # a name with a DB2 table (``emotes`` vs ``Emotes``).
    taken = {name.lower() for name in schemas}

    for view in VIEWS:
        if view.name.lower() in taken:
            skipped.append((view.name, "name collides with a DB2 table"))
            connection.execute(
                "INSERT OR REPLACE INTO wowdb_view VALUES (?, ?, 'skipped', ?)",
                (view.name, view.description, "name collides with a DB2 table"),
            )
            continue

        missing = _missing_dependencies(view, schemas)
        if missing:
            skipped.append((view.name, f"missing {missing}"))
            connection.execute(
                "INSERT OR REPLACE INTO wowdb_view VALUES (?, ?, 'skipped', ?)",
                (view.name, view.description, f"missing {missing}"),
            )
            continue
        try:
            connection.execute(f'CREATE VIEW "{view.name}" AS {view.sql}')
            # Fail loudly here rather than when someone first queries the view.
            connection.execute(f'SELECT * FROM "{view.name}" LIMIT 1').fetchone()
        except sqlite3.Error as exc:
            try:
                connection.execute(f'DROP VIEW IF EXISTS "{view.name}"')
            except sqlite3.Error:
                pass
            skipped.append((view.name, str(exc)))
            connection.execute(
                "INSERT OR REPLACE INTO wowdb_view VALUES (?, ?, 'skipped', ?)",
                (view.name, view.description, str(exc)),
            )
            continue

        created.append(view.name)
        connection.execute(
            "INSERT OR REPLACE INTO wowdb_view VALUES (?, ?, 'ok', NULL)",
            (view.name, view.description),
        )

    connection.commit()
    return created, skipped


def _missing_dependencies(view: View, schemas: dict[str, dict[str, str]]) -> str:
    problems = []
    for table, columns in view.requires.items():
        if table not in schemas:
            problems.append(table)
            continue
        absent = [column for column in columns if column not in schemas[table]]
        if absent:
            problems.append(f"{table}.{{{','.join(absent)}}}")
    return ", ".join(problems)
