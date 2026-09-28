package com.cms.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.Test;

import java.io.File;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.Assert.*;

/**
 * Verifies that the CamelCase json_field aliases added to
 * {@code deployment/scripts/signal_catalog_seed.json} in issue
 * {@code 2026-08-03-sim-actuator-field-mapping} exist and point at the SAME
 * signal_id as their canonical snake_case counterparts.
 *
 * <p>Reading the seed JSON directly (not a live catalog) so this test runs
 * fully offline. If the catalog seed file drifts (rows deleted / signal_id
 * changed) this test fails at CI time before a broken catalog reaches DDB.
 *
 * <p>Companion coverage in the Python-side patch script:
 * {@code deployment/scripts/patch_catalog_actuator_aliases.py}'s
 * {@code build_alias_row()}.
 */
public class SignalCatalogActuatorAliasTest {

    private static final ObjectMapper M = new ObjectMapper();

    /**
     * The 7 simulator-emitted camelCase actuator json_fields → the
     * snake_case json_field that ORIGINALLY existed in the catalog.
     * Same set as {@code ALIASES} in patch_catalog_actuator_aliases.py.
     */
    private static final Map<String, String> EXPECTED_ALIASES = new HashMap<>();
    static {
        EXPECTED_ALIASES.put("allDoorsLocked",   "all_doors_locked");
        EXPECTED_ALIASES.put("chargeDoorOpen",   "charge_door_open");
        EXPECTED_ALIASES.put("doorLFLocked",     "door_frontleft_locked");
        EXPECTED_ALIASES.put("doorRFLocked",     "door_frontright_locked");
        EXPECTED_ALIASES.put("doorLRLocked",     "door_rearleft_locked");
        EXPECTED_ALIASES.put("doorRRLocked",     "door_rearright_locked");
        EXPECTED_ALIASES.put("remoteStartActive","remote_start_active");
    }

    private File findSeedFile() {
        // Test runs from modules/flink; seed lives at repo/deployment/scripts/…
        File cwd = new File("").getAbsoluteFile();
        File candidate = new File(cwd, "../../deployment/scripts/signal_catalog_seed.json");
        assertTrue("Signal catalog seed not found at " + candidate.getAbsolutePath(),
                candidate.exists());
        return candidate;
    }

    @Test
    public void everyCamelCaseAliasHasSameSignalIdAsSnakeCaseCanonical() throws Exception {
        JsonNode rows = M.readTree(findSeedFile());
        assertTrue("Seed file must be a JSON array", rows.isArray());

        // Build json_field -> signal_id lookup
        Map<String, String> byJsonField = new HashMap<>();
        for (JsonNode row : rows) {
            JsonNode jf = row.get("json_field");
            JsonNode sid = row.get("signal_id");
            if (jf == null || sid == null) continue;
            byJsonField.put(jf.asText(), sid.asText());
        }

        for (Map.Entry<String, String> e : EXPECTED_ALIASES.entrySet()) {
            String camel = e.getKey();
            String snake = e.getValue();
            String camelId = byJsonField.get(camel);
            String snakeId = byJsonField.get(snake);
            assertNotNull("Canonical snake_case row missing: " + snake, snakeId);
            assertNotNull("CamelCase alias row missing: " + camel
                    + " (add via patch_catalog_actuator_aliases.py)", camelId);
            assertEquals("Alias " + camel + " must share signal_id with canonical " + snake,
                    snakeId, camelId);
        }
    }

    @Test
    public void aliasRowsMustNotHaveActuatorBlock() throws Exception {
        // Alias rows exposing an actuator block would cause them to appear
        // in commands_lambda._get_catalog (which filters on
        // attribute_exists(actuator)), duplicating each command button in
        // the UI. Guard against a future maintainer copy-pasting the block.
        JsonNode rows = M.readTree(findSeedFile());
        for (JsonNode row : rows) {
            JsonNode name = row.get("signal_name");
            if (name == null) continue;
            if (!name.asText().endsWith("_JsonAlias")) continue;
            assertNull("Alias row " + name.asText() + " must not have `actuator` block "
                    + "(would duplicate UI command button)",
                    row.get("actuator"));
        }
    }

    @Test
    public void aliasRowsMustPreserveVssPathOfCanonical() throws Exception {
        // Decoder manifest generator builds vss_path -> signal_id. If an
        // alias row's vss_path drifts from the canonical row, the dict
        // update could produce a wrong mapping. Sanity: for each alias,
        // its vss_path matches the canonical row's vss_path.
        JsonNode rows = M.readTree(findSeedFile());

        Map<String, String> canonicalVss = new HashMap<>();  // json_field -> vss_path
        for (JsonNode row : rows) {
            JsonNode jf = row.get("json_field");
            JsonNode vp = row.get("vss_path");
            JsonNode name = row.get("signal_name");
            if (jf == null || vp == null || name == null) continue;
            if (name.asText().endsWith("_JsonAlias")) continue;
            canonicalVss.put(jf.asText(), vp.asText());
        }

        for (Map.Entry<String, String> e : EXPECTED_ALIASES.entrySet()) {
            String camel = e.getKey();
            String snake = e.getValue();
            String canonical = canonicalVss.get(snake);
            assertNotNull("Canonical row for " + snake + " has no vss_path", canonical);
            // Locate the alias row with json_field=camel
            for (JsonNode row : rows) {
                JsonNode jf = row.get("json_field");
                JsonNode name = row.get("signal_name");
                if (jf == null || name == null) continue;
                if (!camel.equals(jf.asText())) continue;
                if (!name.asText().endsWith("_JsonAlias")) continue;
                JsonNode vp = row.get("vss_path");
                assertNotNull("Alias " + camel + " missing vss_path", vp);
                assertEquals("Alias " + camel + " vss_path drifted from canonical " + snake,
                        canonical, vp.asText());
            }
        }
    }
}
