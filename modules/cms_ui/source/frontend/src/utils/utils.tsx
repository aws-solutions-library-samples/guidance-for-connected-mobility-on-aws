// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react';
import { Button } from '@cloudscape-design/components';

export function generateRandomDelay(minSec: number, maxSec: number) {
  const minMS = minSec * 1000;
  const maxMS = maxSec * 1000;

  return Math.floor(Math.random() * (maxMS - minMS + 1)) + minMS;
}

export function performRandomDelayAsync(minSec: number, maxSec: number) {
  const delay = generateRandomDelay(minSec, maxSec);
  return new Promise((resolve) => {
    setTimeout(resolve), delay;
  });
}

export function generateRandomNumber(min: number, max: number) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

// Helper function to format dates
// utils.tsx
export function formatDate(date: any): string {
  if (!date) {
    return '-';
  }
  
  // Convert string date to Date object if needed
  const dateObj = typeof date === 'string' ? new Date(date) : date;
  
  // Check if the date is valid
  if (!(dateObj instanceof Date) || isNaN(dateObj.getTime())) {
    return '-';
  }
  
  try {
    // Format: "August 06, 2024 at 09:43 (UTC-04)"
    const options: Intl.DateTimeFormatOptions = {
      year: 'numeric',
      month: 'long',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      timeZoneName: 'short'
    };
    
    const formattedDate = dateObj.toLocaleString('en-US', options);
    
    // Insert "at" before the time part
    return formattedDate.replace(/(\d{4}),\s(\d{2}:\d{2})/, '$1 at $2');
  } catch (error) {
    console.error('Error formatting date:', error);
    return '-';
  }
}

/**
 * Converts a Unix timestamp (in seconds with millisecond precision) to a human-readable date string
 * @param timestamp - Unix timestamp in seconds (e.g., 1722883548.335)
 * @param options - Optional Intl.DateTimeFormatOptions for customizing the output format
 * @returns Formatted date string
 */
export function formatUnixTimestamp(
  timestamp: number, 
  options: Intl.DateTimeFormatOptions = { 
    year: 'numeric', 
    month: 'long', 
    day: '2-digit', 
    hour: '2-digit', 
    minute: '2-digit',
    timeZoneName: 'short'
  }
): string {
  if (!timestamp) return '-';
  
  // Convert seconds to milliseconds for JavaScript Date constructor
  const date = new Date(timestamp * 1000);
  
  // Format: "August 06, 2024 at 09:43 (UTC-04)"
  const formattedDate = new Intl.DateTimeFormat('en-US', options).format(date);
  
  // Insert "at" before the time part
  return formattedDate.replace(/(\d{4}),\s(\d{2}:\d{2})/, '$1 at $2');
}

// Custom CopyToClipboard component with proper Cloudscape styling
export const CustomCopyToClipboard: React.FC<{ text: string; label?: string }> = ({ text, label }) => {
  const [copied, setCopied] = useState(false);
  
  const handleCopy = () => {
    navigator.clipboard.writeText(text).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      },
      (err) => console.error('Could not copy text: ', err)
    );
  };
  
  return (
    <div style={{ display: 'flex', alignItems: 'center' }}>
      <span style={{ marginRight: '8px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {text}
      </span>
      <Button
        variant="icon"
        iconName="copy"
        onClick={handleCopy}
        ariaLabel={label || "Copy to clipboard"}
        title={label || "Copy to clipboard"}
      />
      {copied && <span style={{ marginLeft: '8px', color: 'green' }}>Copied!</span>}
    </div>
  );
};
