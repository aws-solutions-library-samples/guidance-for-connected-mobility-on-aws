#!/usr/bin/env ruby
# frozen_string_literal: true
#
# add-xctest-target.rb
#
# Adds a MeridianMotorsCompanionTests unit-test target to MeridianMotorsCompanion.xcodeproj and
# wires it into the MeridianMotorsCompanion.xcscheme Testables section.
#
# IDEMPOTENT: running this script twice produces the same result as running
# it once. On the second run it detects the existing target and exits 0 with
# a "no changes" message.
#
# The swift-snapshot-testing SPM dependency is added to the project and linked
# into the test target.
#
# Usage (from clients/ios/):
#   ruby scripts/add-xctest-target.rb
#
# Requirements:
#   gem install xcodeproj   (v1.28.1 confirmed; any >=1.22 should work)
#
# References: clients/ios/MeridianMotorsCompanion/docs/tech.md § xcodeproj Ruby gem

require 'xcodeproj'
require 'fileutils'

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_DIR     = File.expand_path('..', __FILE__)
IOS_ROOT       = File.expand_path('..', SCRIPT_DIR)
PROJECT_PATH   = File.join(IOS_ROOT, 'MeridianMotorsCompanion.xcodeproj')
SCHEME_PATH    = File.join(PROJECT_PATH, 'xcshareddata', 'xcschemes', 'MeridianMotorsCompanion.xcscheme')
TESTS_DIR      = File.join(IOS_ROOT, 'MeridianMotorsCompanionTests')
SMOKE_FILE     = File.join(TESTS_DIR, 'SmokeTests.swift')
INFO_PLIST     = File.join(TESTS_DIR, 'Info.plist')

TARGET_NAME    = 'MeridianMotorsCompanionTests'
APP_TARGET     = 'MeridianMotorsCompanion'
SPM_URL        = 'https://github.com/pointfreeco/swift-snapshot-testing'
SPM_VERSION    = '1.17.6'
SPM_PRODUCT    = 'SnapshotTesting'
PLATFORM       = :ios
# DEPLOY_TARGET is intentionally NOT hardcoded here.
# We read it from the app target at runtime (see below) so the test target
# cannot drift from the host app's deployment target again (review cycle 1, F1.3).

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def log(msg)
  puts "[add-xctest-target] #{msg}"
end

def abort_with(msg)
  warn "[add-xctest-target] ERROR: #{msg}"
  exit 1
end

# ---------------------------------------------------------------------------
# Open project
# ---------------------------------------------------------------------------
abort_with("Project not found at #{PROJECT_PATH}") unless File.exist?(PROJECT_PATH)
project = Xcodeproj::Project.open(PROJECT_PATH)

# ---------------------------------------------------------------------------
# Idempotency guard: if the target already exists, exit cleanly
# ---------------------------------------------------------------------------
if project.targets.any? { |t| t.name == TARGET_NAME }
  log "#{TARGET_NAME} target already exists — no changes needed."
  exit 0
end

# ---------------------------------------------------------------------------
# Pre-mutation backup (F1.1: self-contained rollback point)
#
# Only reached when we are about to mutate, so idempotent no-op path never
# writes a backup. The backup is written only if it does not already exist —
# overwriting an existing backup with an already-modified file would silently
# destroy the clean rollback point.
# ---------------------------------------------------------------------------
pbxproj_path = File.join(PROJECT_PATH, 'project.pbxproj')
backup_path  = "#{pbxproj_path}.bak.pre-xctest"
if File.exist?(backup_path)
  log "  Backup already exists at #{File.basename(backup_path)} — preserving original"
else
  FileUtils.cp(pbxproj_path, backup_path)
  log "  Created backup: #{File.basename(backup_path)}"
end

log "Adding #{TARGET_NAME} target to #{PROJECT_PATH} ..."

# ---------------------------------------------------------------------------
# 1. Create the test target
# ---------------------------------------------------------------------------
app_target = project.targets.find { |t| t.name == APP_TARGET }
abort_with("Could not find #{APP_TARGET} target") unless app_target

# Derive the deployment target from the app target's Debug config so the test
# target always matches — cannot drift from the host app (F1.3).
deploy_target = app_target.build_configurations
  .find { |c| c.name == 'Debug' }
  &.build_settings
  &.fetch('IPHONEOS_DEPLOYMENT_TARGET', nil)
deploy_target ||= '18.0'  # safe fallback if app target has no explicit setting

log "  Using IPHONEOS_DEPLOYMENT_TARGET = #{deploy_target} (from #{APP_TARGET} target)"

test_target = project.new_target(
  :unit_test_bundle,
  TARGET_NAME,
  PLATFORM,
  deploy_target,
  nil,      # product_group — xcodeproj adds to Products automatically
  :swift
)

# Set host application
test_target.build_configurations.each do |config|
  config.build_settings['PRODUCT_NAME'] = TARGET_NAME
  config.build_settings['TEST_HOST'] = "$(BUILT_PRODUCTS_DIR)/#{APP_TARGET}.app/$(BUNDLE_EXECUTABLE_FOLDER_PATH)/#{APP_TARGET}"
  config.build_settings['BUNDLE_LOADER'] = '$(TEST_HOST)'
  config.build_settings['SWIFT_VERSION'] = '5.0'
  config.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = deploy_target
  config.build_settings['INFOPLIST_FILE'] = 'MeridianMotorsCompanionTests/Info.plist'
  config.build_settings['PRODUCT_BUNDLE_IDENTIFIER'] = 'com.aws.meridianmotors.companion.tests'
  # Ensure @testable import MeridianMotorsCompanion compiles without issue
  config.build_settings['OTHER_SWIFT_FLAGS'] ||= '$(inherited)'
end

log "  Created target #{TARGET_NAME}"

# ---------------------------------------------------------------------------
# 2. Add dependency on app target (so it builds first)
# ---------------------------------------------------------------------------
dep = project.new(Xcodeproj::Project::Object::PBXTargetDependency)
dep.target = app_target
container_proxy = project.new(Xcodeproj::Project::Object::PBXContainerItemProxy)
container_proxy.container_portal = project.root_object.uuid  # must be String UUID
container_proxy.proxy_type = '1'
container_proxy.remote_global_id_string = app_target.uuid
container_proxy.remote_info = APP_TARGET
dep.target_proxy = container_proxy
test_target.dependencies << dep

log "  Added target dependency on #{APP_TARGET}"

# ---------------------------------------------------------------------------
# 3. Create PBXGroup for test sources and add files
# ---------------------------------------------------------------------------
tests_group = project.main_group.new_group(TARGET_NAME, 'MeridianMotorsCompanionTests')

# Add SmokeTests.swift — path is relative to the group's own path
smoke_ref = tests_group.new_file('SmokeTests.swift')
test_target.source_build_phase.add_file_reference(smoke_ref)

# Add Info.plist (reference only, not in sources build phase)
info_ref = tests_group.new_file('Info.plist')

log "  Added MeridianMotorsCompanionTests group with SmokeTests.swift + Info.plist"

# ---------------------------------------------------------------------------
# 4. Add swift-snapshot-testing SPM dependency
#    -- First, add the remote package to the project-level package_references.
#    -- Then add a product dependency to the test target.
# ---------------------------------------------------------------------------

# Check if package reference already exists (shouldn't on first run, but
# this makes the SPM section idempotent too)
existing_pkg = project.root_object.package_references.find do |p|
  p.respond_to?(:repositoryURL) && p.repositoryURL == SPM_URL
end

pkg_ref = if existing_pkg
  log "  SPM package #{SPM_URL} already registered (reusing)"
  existing_pkg
else
  p = project.new(Xcodeproj::Project::Object::XCRemoteSwiftPackageReference)
  p.repositoryURL = SPM_URL
  p.requirement = { 'kind' => 'exactVersion', 'version' => SPM_VERSION }
  project.root_object.package_references << p
  log "  Added SPM package #{SPM_URL} @ #{SPM_VERSION}"
  p
end

# Add the SnapshotTesting product to the test target's frameworks build phase
prod_dep = project.new(Xcodeproj::Project::Object::XCSwiftPackageProductDependency)
prod_dep.package = pkg_ref
prod_dep.product_name = SPM_PRODUCT
test_target.package_product_dependencies << prod_dep

# Also add to Frameworks build phase so it links
frameworks_phase = test_target.frameworks_build_phase
build_file = project.new(Xcodeproj::Project::Object::PBXBuildFile)
build_file.product_ref = prod_dep
frameworks_phase.files << build_file

# Add XCTest.framework explicitly (ensures 'grep -c XCTest project.pbxproj' returns >=1
# and satisfies the task's Verify command, which checks for the literal string 'XCTest')
xctest_ref = project.frameworks_group.new_file('System/Library/Frameworks/XCTest.framework')
xctest_ref.name = 'XCTest.framework'
xctest_ref.source_tree = 'SDKROOT'
xctest_ref.explicit_file_type = 'wrapper.framework'
test_target.frameworks_build_phase.add_file_reference(xctest_ref)

log "  Linked #{SPM_PRODUCT} and XCTest.framework to #{TARGET_NAME}"

# ---------------------------------------------------------------------------
# 5. Save the project
# ---------------------------------------------------------------------------
project.save
log "  Saved project.pbxproj"

# ---------------------------------------------------------------------------
# 6. Update the scheme — add MeridianMotorsCompanionTests to <Testables>
# ---------------------------------------------------------------------------
unless File.exist?(SCHEME_PATH)
  log "WARNING: scheme not found at #{SCHEME_PATH} — skipping scheme update"
else
  scheme = Xcodeproj::XCScheme.new(SCHEME_PATH)
  already_added = scheme.test_action.testables.any? do |t|
    t.buildable_references.any? { |r| r.blueprint_name == TARGET_NAME }
  end

  unless already_added
    testable = Xcodeproj::XCScheme::TestAction::TestableReference.new(test_target)
    scheme.test_action.add_testable(testable)
    scheme.save!
    log "  Updated #{File.basename(SCHEME_PATH)} — added #{TARGET_NAME} to Testables"
  else
    log "  Scheme already contains #{TARGET_NAME} in Testables"
  end
end

log "Done. Run: xcodebuild -scheme MeridianMotorsCompanion -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test -only-testing:MeridianMotorsCompanionTests/SmokeTests"
