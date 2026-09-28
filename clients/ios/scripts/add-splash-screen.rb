#!/usr/bin/env ruby
# frozen_string_literal: true

# add-splash-screen.rb — registers the Meridian launch-splash files with
# MeridianMotorsCompanion.xcodeproj.
#
# Idempotent: running twice does nothing if already added. Safe to re-run.
#
# Adds:
#   MeridianMotorsCompanion/Views/Splash/SplashView.swift         → app target (Sources)
#   MeridianMotorsCompanion/Views/Splash/SplashVideoPlayer.swift  → app target (Sources)
#   MeridianMotorsCompanion/LaunchScreen.storyboard               → app target (Resources)
#   MeridianMotorsCompanion/Resources/meridian_splash.mp4         → app target (Resources)
#   MeridianMotorsCompanionTests/UI/SplashPlanTests.swift         → test target (Sources)
#
# Why the .mp4 is registered individually rather than by folder reference (the
# approach add-vehicle-sweep.rb uses for the *Sweep frame directories): the
# sweeps are read with `Bundle.main.url(forResource:withExtension:subdirectory:)`
# and need their directory preserved inside the bundle. The splash clip is read
# WITHOUT a subdirectory — `url(forResource: "meridian_splash", withExtension:
# "mp4")` — so it must land flat at the bundle root. Adding Resources/ as a
# folder reference would nest it and that lookup would return nil, silently
# demoting every launch to the static-logo fallback.
#
# Usage:  ruby scripts/add-splash-screen.rb [--dry-run] [--allow-xcode-running]
#
# IMPORTANT: close Xcode before running. Xcode holds an in-memory copy of
# project.pbxproj and overwrites external edits on its next save.
#
# `--allow-xcode-running` overrides that check. Only correct when Xcode is
# running WITHOUT this project open — verify first, don't assume:
#
#     lsof -c Xcode | grep MeridianMotorsCompanion.xcodeproj    # must print nothing
#
# The hazard is Xcode's in-memory copy of THIS project, so an Xcode editing
# something else is harmless. An empty result above is the evidence; the
# process check alone cannot tell the two cases apart.

require 'xcodeproj'

PROJECT_PATH = File.expand_path('../MeridianMotorsCompanion.xcodeproj', __dir__)
APP_TARGET   = 'MeridianMotorsCompanion'
TEST_TARGET  = 'MeridianMotorsCompanionTests'
IOS_DIR      = File.expand_path('..', __dir__)
DRY_RUN      = ARGV.include?('--dry-run')
ALLOW_XCODE  = ARGV.include?('--allow-xcode-running')

if !DRY_RUN && !ALLOW_XCODE && system('pgrep -x Xcode > /dev/null 2>&1')
  abort(
    "\u2717 Xcode is running. Quit Xcode and re-run (or pass --dry-run to preview).\n" \
    "  If Xcode is open WITHOUT this project, confirm with\n" \
    "    lsof -c Xcode | grep MeridianMotorsCompanion.xcodeproj\n" \
    "  and if that prints nothing, re-run with --allow-xcode-running."
  )
end

project = Xcodeproj::Project.open(PROJECT_PATH)

def target!(project, name)
  project.targets.find { |t| t.name == name } or
    abort("\u2717 target '#{name}' not found (have: #{project.targets.map(&:name).join(', ')})")
end

app_target  = target!(project, APP_TARGET)
test_target = target!(project, TEST_TARGET)

def find_or_create_group(project, parts)
  group = project.main_group
  parts.each do |part|
    child = group.children.find do |c|
      c.is_a?(Xcodeproj::Project::Object::PBXGroup) && c.display_name == part
    end
    group = child || group.new_group(part, part)
  end
  group
end

# Files to register: [group path parts, filename, target, build phase]
ENTRIES = [
  [%w[MeridianMotorsCompanion Views Splash], 'SplashView.swift',        :app,  :sources],
  [%w[MeridianMotorsCompanion Views Splash], 'SplashVideoPlayer.swift', :app,  :sources],
  [%w[MeridianMotorsCompanion],              'LaunchScreen.storyboard', :app,  :resources],
  [%w[MeridianMotorsCompanion Resources],    'meridian_splash.mp4',     :app,  :resources],
  [%w[MeridianMotorsCompanionTests UI],      'SplashPlanTests.swift',   :test, :sources]
].freeze

changed = 0

ENTRIES.each do |parts, filename, which_target, phase|
  target    = which_target == :app ? app_target : test_target
  disk_path = File.join(IOS_DIR, *parts, filename)

  unless File.exist?(disk_path)
    abort("\u2717 disk file not found: #{disk_path}")
  end

  group = find_or_create_group(project, parts)

  if group.children.any? { |c| c.display_name == filename }
    puts "  already present: #{parts.join('/')}/#{filename}"
    next
  end

  unless DRY_RUN
    ref = group.new_file(filename)
    ref.set_source_tree('<group>')
    case phase
    when :sources   then target.source_build_phase.add_file_reference(ref)
    when :resources then target.resources_build_phase.add_file_reference(ref)
    end
  end

  puts "  #{DRY_RUN ? 'would add' : 'added'}: #{parts.join('/')}/#{filename} " \
       "(#{phase == :sources ? 'Sources' : 'Resources'} → #{target.name})"
  changed += 1
end

if changed.zero?
  puts 'No changes needed.'
elsif DRY_RUN
  puts "\n(dry run — no changes written; #{changed} file(s) would be added)"
else
  project.save
  puts "\n\u2713 project saved (#{changed} change(s))."
end
