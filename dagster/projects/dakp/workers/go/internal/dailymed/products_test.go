package dailymed

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

// TestMockProducts exercises the simplified fixture's <product> elements: substance and
// moiety rows, two products on one set (a brand and a self-named generic).
func TestMockProducts(t *testing.T) {
	docs, err := ParseFile(mockFixture)
	if err != nil {
		t.Fatalf("ParseFile: %v", err)
	}
	wantDoc0 := []ProductIngredient{
		{ProductCode: "00001-0001", ProductName: "Exastat", GenericName: "Examplestatin", IngredientName: "Examplestatin sodium", IngredientUNII: "UNII:QFX8B1R4QF", Kind: "substance"},
		{ProductCode: "00001-0001", ProductName: "Exastat", GenericName: "Examplestatin", IngredientName: "Examplestatin", IngredientUNII: "UNII:QFX8B1R4QF", Kind: "moiety"},
	}
	if !reflect.DeepEqual(docs[0].Products, wantDoc0) {
		t.Errorf("doc0.Products = %+v, want %+v", docs[0].Products, wantDoc0)
	}
	if len(docs[1].Products) != 2 {
		t.Fatalf("doc1.Products = %+v, want 2 (Advil + the self-named generic)", docs[1].Products)
	}
	if docs[1].Products[1].ProductName != "Ibuprofen" || docs[1].Products[1].IngredientName != "Ibuprofen" {
		t.Errorf("doc1 second product = %+v, want the self-named Ibuprofen generic", docs[1].Products[1])
	}
	if got := docs[2].Products[0].ProductName; got != "Prilosec" {
		t.Errorf("doc2 product name = %q, want Prilosec", got)
	}
}

// TestHL7v3Products exercises the modern manufacturedProduct/manufacturedProduct shape:
// ACTIB actives (substance + activeMoiety), with IACT/CNTM entries excluded.
func TestHL7v3Products(t *testing.T) {
	docs, err := ParseFile("testdata/hl7v3_spl.xml.gz")
	if err != nil {
		t.Fatalf("ParseFile: %v", err)
	}
	want := []ProductIngredient{
		{ProductCode: "63187-050", ProductName: "Lisinopril and hydrochlorothiazide 20/12.5", GenericName: "Lisinopril and hydrochlorothiazide", IngredientName: "LISINOPRIL", IngredientUNII: "UNII:E7199S1YWR", Kind: "substance"},
		{ProductCode: "63187-050", ProductName: "Lisinopril and hydrochlorothiazide 20/12.5", GenericName: "Lisinopril and hydrochlorothiazide", IngredientName: "LISINOPRIL ANHYDROUS", IngredientUNII: "UNII:7Q3P4BS2FD", Kind: "moiety"},
		{ProductCode: "63187-050", ProductName: "Lisinopril and hydrochlorothiazide 20/12.5", GenericName: "Lisinopril and hydrochlorothiazide", IngredientName: "HYDROCHLOROTHIAZIDE", IngredientUNII: "UNII:0J48LPH2TH", Kind: "substance"},
	}
	if !reflect.DeepEqual(docs[0].Products, want) {
		t.Errorf("Products =\n%+v\nwant\n%+v", docs[0].Products, want)
	}
}

func TestHL7v3ProductBranches(t *testing.T) {
	t.Run("legacy manufacturedMedicine", func(t *testing.T) {
		docs := parseHL7v3String(t, `<document><setId root="S"/>`+
			`<subject><manufacturedProduct><manufacturedMedicine>`+
			`<code code="0069-3750" codeSystem="2.16.840.1.113883.6.69"/>`+
			`<name>Renese</name>`+
			`<activeIngredient><activeIngredientSubstance>`+
			`<code code="36780APV5N"/><name>polythiazide</name>`+
			`<activeMoiety><activeMoiety><code code="36780APV5N"/><name>polythiazide</name></activeMoiety></activeMoiety>`+
			`</activeIngredientSubstance></activeIngredient>`+
			`<asEntityWithGeneric><genericMedicine><name>polythiazide</name></genericMedicine></asEntityWithGeneric>`+
			`</manufacturedMedicine></manufacturedProduct></subject></document>`)
		want := []ProductIngredient{
			{ProductCode: "0069-3750", ProductName: "Renese", GenericName: "polythiazide", IngredientName: "polythiazide", IngredientUNII: "UNII:36780APV5N", Kind: "substance"},
			{ProductCode: "0069-3750", ProductName: "Renese", GenericName: "polythiazide", IngredientName: "polythiazide", IngredientUNII: "UNII:36780APV5N", Kind: "moiety"},
		}
		if !reflect.DeepEqual(docs[0].Products, want) {
			t.Errorf("Products = %+v, want %+v", docs[0].Products, want)
		}
	})

	t.Run("kit parts attach to the kit product", func(t *testing.T) {
		docs := parseHL7v3String(t, `<document><setId root="S"/>`+
			`<manufacturedProduct><manufacturedProduct>`+
			`<code code="49836-009"/><name>P-Care K80G</name>`+
			`<part><partProduct><name>Aplicare Povidone-Iodine Triples</name>`+
			`<ingredient classCode="ACTIM"><ingredientSubstance>`+
			`<code code="85H0HZU99M"/><name>POVIDONE-IODINE</name>`+
			`<activeMoiety><activeMoiety><code code="9679TC07X4"/><name>IODINE</name></activeMoiety></activeMoiety>`+
			`</ingredientSubstance></ingredient></partProduct></part>`+
			`<part><partProduct><name>Second Part</name>`+
			`<ingredient classCode="ACTIR"><ingredientSubstance>`+
			`<code code="ABC"/><name>ALCOHOL</name>`+
			`</ingredientSubstance></ingredient></partProduct></part>`+
			`</manufacturedProduct></manufacturedProduct></document>`)
		got := docs[0].Products
		if len(got) != 3 {
			t.Fatalf("Products = %+v, want 3 (povidone substance + iodine moiety + alcohol)", got)
		}
		for _, p := range got {
			if p.ProductName != "P-Care K80G" {
				t.Errorf("part ingredient attached to %q, want the kit product P-Care K80G", p.ProductName)
			}
		}
		if got[2].IngredientName != "ALCOHOL" {
			t.Errorf("ACTIR ingredient = %+v, want ALCOHOL", got[2])
		}
	})

	t.Run("unnamed products and missing ingredient names are skipped", func(t *testing.T) {
		docs := parseHL7v3String(t, `<document><setId root="S"/>`+
			`<manufacturedProduct><manufacturedProduct><code code="1"/></manufacturedProduct></manufacturedProduct>`+
			`<manufacturedProduct><name>Named</name>`+
			`<ingredient classCode="ACTIB"><ingredientSubstance><code code="X"/></ingredientSubstance></ingredient>`+
			`</manufacturedProduct></document>`)
		if len(docs[0].Products) != 0 {
			t.Errorf("Products = %+v, want none", docs[0].Products)
		}
	})

	t.Run("duplicate substance across strengths dedups", func(t *testing.T) {
		docs := parseHL7v3String(t, `<document><setId root="S"/>`+
			`<manufacturedProduct><name>Drug</name>`+
			`<ingredient classCode="ACTIB"><ingredientSubstance><code code="A"/><name>Foo</name></ingredientSubstance></ingredient>`+
			`<ingredient classCode="ACTIM"><ingredientSubstance><code code="A"/><name>foo</name></ingredientSubstance></ingredient>`+
			`</manufacturedProduct></document>`)
		if len(docs[0].Products) != 1 {
			t.Errorf("Products = %+v, want 1 (case-insensitive dedup)", docs[0].Products)
		}
	})
}

// TestProductRowsGoldenShape pins the row contract: column order, source_record_id kind and
// the release_file/xml_path duplication.
func TestProductRowsGoldenShape(t *testing.T) {
	tables := extractFixture(t, 0)
	tf := TableFile{}
	for _, f := range tables.TableFiles() {
		if f.Name == "spl_products.tsv" {
			tf = f
		}
	}
	if !reflect.DeepEqual(tf.Columns, ProductsColumns) {
		t.Fatalf("columns = %v", tf.Columns)
	}
	if len(tf.Rows) != 5 {
		t.Fatalf("rows = %d, want 5 (2 Exastat + 2 Ibuprofen-set + 1 Prilosec)", len(tf.Rows))
	}
	for _, row := range tf.Rows {
		if len(row) != len(ProductsColumns) {
			t.Errorf("row width %d != %d", len(row), len(ProductsColumns))
		}
	}
	golden, err := os.ReadFile(filepath.Join("testdata", "golden", "spl_products.tsv"))
	if err != nil {
		t.Fatalf("read golden: %v", err)
	}
	if got := RenderTSV(tf.Columns, tf.Rows); got != string(golden) {
		t.Errorf("products TSV != golden\n--- got ---\n%s\n--- want ---\n%s", got, golden)
	}
}
