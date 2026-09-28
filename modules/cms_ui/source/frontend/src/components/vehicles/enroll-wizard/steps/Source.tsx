// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react';
import {
  SpaceBetween,
  FormField,
  Textarea,
  Button,
  Alert,
  Box,
  FileUpload,
} from '@cloudscape-design/components';
import type { WizardAction } from '../state/reducer';

const VIN_RE = /^[A-HJ-NPR-Z0-9]{17}$/i;

/** Parse VINs from a plain-text string (comma or newline separated). */
function parseVinsFromText(raw: string): { vins: string[]; errors: string[] } {
  const vins: string[] = [];
  const errors: string[] = [];
  raw
    // strip BOM
    .replace(/^\uFEFF/, '')
    .split(/[\r\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .forEach((vin) => {
      if (VIN_RE.test(vin)) {
        vins.push(vin.toUpperCase());
      } else {
        errors.push(vin);
      }
    });
  return { vins: [...new Set(vins)], errors };
}

/** Parse VINs from a CSV file (expects a `vin` column; BOM/CRLF tolerant per NG10/C16). */
async function parseVinsFromCsv(file: File): Promise<{ vins: string[]; errors: string[] }> {
  const text = await file.text();
  // strip BOM, normalise line endings
  const clean = text.replace(/^\uFEFF/, '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const lines = clean.split('\n').filter((l) => l.trim().length > 0);
  if (lines.length === 0) return { vins: [], errors: [] };

  const header = lines[0].split(',').map((h) => h.trim().toLowerCase());
  const vinCol = header.indexOf('vin');
  if (vinCol === -1) return { vins: [], errors: ['CSV must contain a `vin` column'] };

  const vins: string[] = [];
  const errors: string[] = [];
  lines.slice(1).forEach((line) => {
    const cols = line.split(',');
    const vin = (cols[vinCol] ?? '').trim();
    if (!vin) return;
    if (VIN_RE.test(vin)) {
      vins.push(vin.toUpperCase());
    } else {
      errors.push(vin);
    }
  });
  return { vins: [...new Set(vins)], errors };
}

interface StepSourceProps {
  currentVins: string[];
  dispatch: React.Dispatch<WizardAction>;
}

const StepSource: React.FC<StepSourceProps> = ({ currentVins, dispatch }) => {
  const [text, setText] = useState(currentVins.join('\n'));
  const [parseErrors, setParseErrors] = useState<string[]>([]);
  const [csvFiles, setCsvFiles] = useState<File[]>([]);

  function commitText() {
    const { vins, errors } = parseVinsFromText(text);
    setParseErrors(errors);
    if (vins.length > 0) {
      dispatch({ type: 'SET_VINS', vins });
    }
  }

  async function onCsvChange(files: File[]) {
    setCsvFiles(files);
    if (files.length === 0) return;
    const { vins, errors } = await parseVinsFromCsv(files[0]);
    setParseErrors(errors);
    if (vins.length > 0) {
      setText(vins.join('\n'));
      dispatch({ type: 'SET_VINS', vins });
    }
  }

  return (
    <SpaceBetween size="m">
      <FormField
        label="Paste VINs"
        description="One per line, or comma-separated. Duplicates are de-duplicated automatically."
        errorText={parseErrors.length > 0 ? `${parseErrors.length} invalid VIN(s) ignored: ${parseErrors.slice(0, 3).join(', ')}${parseErrors.length > 3 ? '…' : ''}` : undefined}
      >
        <Textarea
          value={text}
          onChange={({ detail }) => setText(detail.value)}
          onBlur={commitText}
          placeholder="1FTFW1E16JFD55835&#10;3FA6P0D9XKR153122"
          rows={8}
        />
      </FormField>

      <Box>
        <Button onClick={commitText} variant="inline-link">
          Validate VINs
        </Button>
      </Box>

      <FormField
        label="Or upload CSV"
        description='CSV must include a "vin" column. Extra columns are ignored (NG10).'
      >
        <FileUpload
          value={csvFiles}
          onChange={({ detail }) => void onCsvChange(detail.value)}
          accept=".csv,text/csv"
          i18nStrings={{
            uploadButtonText: () => 'Choose CSV',
            dropzoneText: () => 'Drop CSV here',
            removeFileAriaLabel: (i) => `Remove file ${i + 1}`,
            limitShowFewer: 'Show fewer',
            limitShowMore: 'Show more',
            errorIconAriaLabel: 'Error',
          }}
        />
      </FormField>

      {currentVins.length > 0 && (
        <Alert type="success">{currentVins.length} VIN(s) ready for pre-flight.</Alert>
      )}
    </SpaceBetween>
  );
};

export default StepSource;
