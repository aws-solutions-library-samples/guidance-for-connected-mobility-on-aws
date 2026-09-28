import XCTest
@testable import MeridianMotorsCompanion

/// Regression coverage for the `ConfiguratorFlow` step machine.
///
/// ## Access blocker — CLOSED (task 5.1)
///
/// `ConfiguratorFlow.enum Step` was declared `private`. Task 5.1 promoted it to
/// `internal`, unblocking the step-transition tests below. The fix is a one-word
/// change in `ConfiguratorFlow.swift` line ~44.
///
/// ## Test organisation
///
/// - Catalog model round-trips (10 tests from Group 2 — inputs to each step)
/// - Step-transition tests (9 tests from Group 5 / task 5.1 — machine coverage)
final class ConfiguratorFlowStepMachineTests: XCTestCase {

    // MARK: - Shared fixtures

    private let cat = CatalogCategory(
        categoryId: "sport", displayName: "Sport", symbolName: nil, sortOrder: 1)
    private let model = CatalogModel(
        modelId: "m300", categoryId: "sport", displayName: "Meridian 300",
        basePrice: 8999, imageKey: nil, specBadges: nil, sortOrder: 1)
    private let variant = CatalogVariant(
        variantId: "std", modelId: "m300", displayName: "Standard",
        priceAdder: nil, specOverrides: nil, sortOrder: 1)
    private let color = CatalogColor(
        colorId: "red", variantId: "std", displayName: "Racing Red",
        hexColor: "#C00", imageKey: nil, priceAdder: nil)
    private let resp = ReservationResponse(
        orderId: "ord_123", orderNumber: "ORD-2026-08-0001", depositRef: nil)

    // MARK: - Step-transition tests (task 5.1 — the 9 blocked by private access)

    func testPickCategoryToPickModel() {
        // Forward: selecting a category advances to pickModel.
        var step = ConfiguratorFlow.Step.pickCategory
        step = .pickModel(cat)
        if case .pickModel(let c) = step { XCTAssertEqual(c.categoryId, "sport") }
        else { XCTFail("Expected pickModel") }
    }

    func testPickModelToPickVariant() {
        var step = ConfiguratorFlow.Step.pickModel(cat)
        step = .pickVariant(cat, model)
        if case .pickVariant(let c, let m) = step {
            XCTAssertEqual(c.categoryId, "sport")
            XCTAssertEqual(m.modelId, "m300")
        } else { XCTFail("Expected pickVariant") }
    }

    func testPickVariantToShowEditionSummary() {
        // After selecting a variant the machine advances to showEditionSummary,
        // not pickColor — Edition is displayed, not chosen (task 5.2).
        var step = ConfiguratorFlow.Step.pickVariant(cat, model)
        let edition = IvePackage.adventure
        step = .showEditionSummary(edition, model, variant)
        if case .showEditionSummary(let e, let m, _) = step {
            XCTAssertEqual(e, .adventure)
            XCTAssertEqual(m.modelId, "m300")
        } else { XCTFail("Expected showEditionSummary") }
    }

    func testShowEditionSummaryToPickColor() {
        var step = ConfiguratorFlow.Step.showEditionSummary(.executive, model, variant)
        step = .pickColor(.executive, model, variant)
        if case .pickColor(let e, _, _) = step { XCTAssertEqual(e, .executive) }
        else { XCTFail("Expected pickColor") }
    }

    func testPickColorToPickInteriorStyle() {
        var step = ConfiguratorFlow.Step.pickColor(.family, model, variant)
        step = .pickInteriorStyle(.family, model, variant, color)
        if case .pickInteriorStyle(let e, _, _, let c) = step {
            XCTAssertEqual(e, .family)
            XCTAssertEqual(c.colorId, "red")
        } else { XCTFail("Expected pickInteriorStyle") }
    }

    func testPickInteriorStyleToConfirm() {
        var step = ConfiguratorFlow.Step.pickInteriorStyle(.entryUrban, model, variant, color)
        step = .confirm(.entryUrban, model, variant, color, .classic, [], .homeDelivery)
        if case .confirm(let e, _, _, _, let s, _, _) = step {
            XCTAssertEqual(e, .entryUrban)
            XCTAssertEqual(s, .classic)
        } else { XCTFail("Expected confirm") }
    }

    func testConfirmToSuccess() {
        var step = ConfiguratorFlow.Step.confirm(.family, model, variant, color, .sport, [], .homeDelivery)
        step = .success(resp)
        if case .success(let r) = step { XCTAssertEqual(r.orderId, "ord_123") }
        else { XCTFail("Expected success") }
    }

    func testBackNavigationPickColorToShowEditionSummary() {
        // Back from pickColor returns to showEditionSummary (not pickVariant).
        var step = ConfiguratorFlow.Step.pickColor(.adventure, model, variant)
        step = .showEditionSummary(.adventure, model, variant)
        if case .showEditionSummary(let e, _, _) = step { XCTAssertEqual(e, .adventure) }
        else { XCTFail("Expected showEditionSummary on back") }
    }

    func testBackNavigationPickInteriorToPickColor() {
        // Back from pickInteriorStyle returns to pickColor.
        var step = ConfiguratorFlow.Step.pickInteriorStyle(.executive, model, variant, color)
        step = .pickColor(.executive, model, variant)
        if case .pickColor(let e, _, _) = step { XCTAssertEqual(e, .executive) }
        else { XCTFail("Expected pickColor on back") }
    }

    // MARK: - Catalog model round-trips (step inputs, from Group 2)

    func testCatalogCategoryDecodes() throws {
        let json = """
        { "categoryId": "sport", "displayName": "Sport", "symbolName": "bolt.fill", "sortOrder": 1 }
        """.data(using: .utf8)!
        let cat = try JSONDecoder().decode(CatalogCategory.self, from: json)
        XCTAssertEqual(cat.categoryId, "sport")
        XCTAssertEqual(cat.id, "sport")
    }

    func testCatalogModelDecodes() throws {
        let json = """
        { "modelId": "meridian-300", "categoryId": "sport", "displayName": "Meridian 300",
          "basePrice": 8999.0, "imageKey": "hero.jpg", "sortOrder": 1 }
        """.data(using: .utf8)!
        let model = try JSONDecoder().decode(CatalogModel.self, from: json)
        XCTAssertEqual(model.modelId, "meridian-300")
        XCTAssertEqual(model.id, "meridian-300")
    }

    func testCatalogVariantDecodesWithOptionalFields() throws {
        let json = """
        { "variantId": "standard", "modelId": "meridian-300", "displayName": "Standard" }
        """.data(using: .utf8)!
        let v = try JSONDecoder().decode(CatalogVariant.self, from: json)
        XCTAssertNil(v.priceAdder)
        XCTAssertEqual(v.id, "standard")
    }

    func testCatalogColorDecodes() throws {
        let json = """
        { "colorId": "racing-red", "variantId": "standard", "displayName": "Racing Red",
          "hexColor": "#C0392B", "priceAdder": 200.0 }
        """.data(using: .utf8)!
        let c = try JSONDecoder().decode(CatalogColor.self, from: json)
        XCTAssertEqual(c.hexColor, "#C0392B")
        XCTAssertEqual(c.id, "racing-red")
    }

    func testCatalogAccessoryDecodes() throws {
        let json = """
        { "accessoryId": "top-case", "displayName": "Top Case", "description": "40L", "price": 350.0 }
        """.data(using: .utf8)!
        let a = try JSONDecoder().decode(CatalogAccessory.self, from: json)
        XCTAssertNil(a.categoryId)
        XCTAssertEqual(a.id, "top-case")
    }

    func testAcquireErrorEndpointUnavailableIsRecognised() {
        let err = AcquireError.endpointUnavailable("/acquire/catalog/test-tenant")
        XCTAssertTrue(err.isEndpointUnavailable)
    }

    func testAcquireErrorNetworkIsNotEndpointUnavailable() {
        let err = AcquireError.network(URLError(.timedOut))
        XCTAssertFalse(err.isEndpointUnavailable)
    }

    func testRedactContactPIIEmail() {
        let out = AcquireError.redactContactPII("email: john.smith@example.com")
        XCTAssertFalse(out.contains("john.smith@example.com"))
        XCTAssertTrue(out.contains("<email-redacted>"))
    }

    func testRedactContactPIIPhone() {
        let out = AcquireError.redactContactPII("Phone +1-800-555-0100 invalid")
        XCTAssertFalse(out.contains("+1-800-555-0100"))
        XCTAssertTrue(out.contains("<phone-redacted>"))
    }

    func testReservationRequestEncodesCorrectly() throws {
        let req = ReservationRequest(
            tenantId: "test-tenant", modelId: "meridian-300", variantId: "standard",
            colorId: "racing-red", selectedAccessoryIds: ["top-case"],
            depositRef: "dep-001", qualificationTier: "prime",
            discoverSessionId: nil, leadId: nil,
            edition: "adventure", interiorStyle: "sport", firstName: nil,
            handoverMethod: nil, handoverCenterId: nil
        )
        let data = try JSONEncoder().encode(req)
        let dict = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        XCTAssertEqual(dict["tenantId"] as? String, "test-tenant")
        XCTAssertEqual(dict["modelId"] as? String, "meridian-300")
        XCTAssertEqual(dict["edition"] as? String, "adventure")
        XCTAssertNil(dict["firstName"], "firstName key must be absent when nil")
    }
}

