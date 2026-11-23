import { handleGlassMenuKey } from "../../components/glass-menu-keyboard";
import { SelectMenu } from "../../components/SelectMenu";

const options = [
  { value: "updated:desc", label: "Updated" },
  { value: "applied:desc", label: "Date applied" },
  { value: "company:asc", label: "Company A–Z" },
];

export function ApplicationsSort() {
  return (
    <div className="tracker-sort-control" onKeyDown={handleGlassMenuKey}>
      <SelectMenu label="Sort applications" choices={options} className="tracker-sort-menu" align="end"
        selectProps={{ id: "tracker-sort" }} />
    </div>
  );
}
